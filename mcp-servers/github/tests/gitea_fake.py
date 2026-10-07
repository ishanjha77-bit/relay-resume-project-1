"""An in-memory Gitea: just the endpoints Relay uses, with the shapes the real
API returns (recorded from Gitea 28 while building this server)."""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote

import httpx2

REPO = "shop/deploy"
T0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


def blob_sha(content: str) -> str:
    data = content.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


@dataclass
class Commit:
    sha: str
    parent: str | None
    message: str
    author: str
    time: datetime
    tree: dict[str, str]


@dataclass
class FakeGitea:
    commits: dict[str, Commit] = field(default_factory=dict)
    branches: dict[str, str] = field(default_factory=dict)
    pulls: list[dict] = field(default_factory=list)
    requests: list[str] = field(default_factory=list)
    protected: set[str] = field(default_factory=lambda: {"main"})

    def commit(
        self, branch: str, files: dict[str, str], message: str, author: str = "ci-bot", minutes: int = 0
    ) -> str:
        parent = self.branches.get(branch)
        tree = dict(self.commits[parent].tree) if parent else {}
        tree.update(files)
        sha = hashlib.sha1(f"{parent}{message}{len(self.commits)}".encode()).hexdigest()
        self.commits[sha] = Commit(sha, parent, message + "\n", author, T0 + timedelta(minutes=minutes), tree)
        self.branches[branch] = sha
        return sha

    def resolve(self, ref: str) -> Commit:
        return self.commits[self.branches.get(ref, ref)]

    def history(self, ref: str) -> list[Commit]:
        out, sha = [], self.branches[ref]
        while sha:
            out.append(self.commits[sha])
            sha = self.commits[sha].parent
        return out

    def changed(self, c: Commit) -> list[str]:
        before = self.commits[c.parent].tree if c.parent else {}
        return sorted(p for p in set(before) | set(c.tree) if before.get(p) != c.tree.get(p))

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        path = unquote(request.url.path).removeprefix(f"/api/v1/repos/{REPO}")
        self.requests.append(f"{request.method} {path}")
        q = request.url.params
        if request.method == "GET" and path == "/commits":
            since, until = datetime.fromisoformat(q["since"]), datetime.fromisoformat(q["until"])
            rows = [
                c
                for c in self.history(q["sha"])
                if since <= c.time <= until and (not q.get("path") or q["path"] in self.changed(c))
            ][: int(q["limit"])]
            return httpx2.Response(200, json=[self._commit_json(c) for c in rows])
        if request.method == "GET" and path.startswith("/git/commits/"):
            ref = path.removeprefix("/git/commits/")
            if ref.endswith(".diff"):
                return httpx2.Response(200, text=self._diff(self.commits[ref.removesuffix(".diff")]))
            c = self.commits[ref]
            return httpx2.Response(
                200, json={"sha": c.sha, "parents": [{"sha": c.parent}] if c.parent else []}
            )
        if path.startswith("/contents/"):
            file = path.removeprefix("/contents/")
            if request.method == "GET":
                tree = self.resolve(q.get("ref", "main")).tree
                if file not in tree:
                    return httpx2.Response(404, json={"message": "object does not exist"})
                content = tree[file]
                return httpx2.Response(
                    200,
                    json={
                        "path": file,
                        "sha": blob_sha(content),
                        "encoding": "base64",
                        "content": base64.b64encode(content.encode()).decode(),
                    },
                )
            body = json.loads(request.content)
            head = self.resolve(body["branch"])
            if blob_sha(head.tree[file]) != body["sha"]:
                return httpx2.Response(409, json={"message": "sha does not match"})
            target = body.get("new_branch") or body["branch"]
            if target in self.protected:
                return httpx2.Response(403, json={"message": "user cannot commit to repo"})
            self.branches[target] = head.sha
            sha = self.commit(
                target, {file: base64.b64decode(body["content"]).decode()}, body["message"], "relay-bot"
            )
            return httpx2.Response(200, json={"commit": {"sha": sha}})
        if request.method == "GET" and path.startswith("/pulls/main/"):
            branch = path.removeprefix("/pulls/main/")
            pull = next((p for p in self.pulls if p["head"]["ref"] == branch), None)
            return (
                httpx2.Response(200, json=pull)
                if pull
                else httpx2.Response(404, json={"message": "not found"})
            )
        if request.method == "POST" and path == "/pulls":
            body = json.loads(request.content)
            number = len(self.pulls) + 1
            pull = {
                "number": number,
                "title": body["title"],
                "body": body["body"],
                "html_url": f"http://gitea:3000/{REPO}/pulls/{number}",
                "head": {"ref": body["head"]},
                "base": {"ref": body["base"]},
                "draft": body["title"].startswith("WIP:"),
                "state": "open",
            }
            self.pulls.append(pull)
            return httpx2.Response(201, json=pull)
        return httpx2.Response(404, json={"message": f"fake has no {request.method} {path}"})

    def _commit_json(self, c: Commit) -> dict:
        when = c.time.strftime("%Y-%m-%dT%H:%M:%SZ")
        person = {"name": c.author, "email": f"{c.author}@shop.local", "date": when}
        return {
            "sha": c.sha,
            "html_url": f"http://gitea:3000/{REPO}/commit/{c.sha}",
            "created": when,
            "commit": {"author": person, "committer": person, "message": c.message},
            "files": [{"filename": f, "status": "modified"} for f in self.changed(c)],
        }

    def _diff(self, c: Commit) -> str:
        before = self.commits[c.parent].tree if c.parent else {}
        out = []
        for f in self.changed(c):
            out.append(f"diff --git a/{f} b/{f}\n")
            out.extend(
                difflib.unified_diff(
                    before.get(f, "").splitlines(keepends=True),
                    c.tree.get(f, "").splitlines(keepends=True),
                    f"a/{f}",
                    f"b/{f}",
                )
            )
        return "".join(out)
