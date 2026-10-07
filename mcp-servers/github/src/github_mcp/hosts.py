"""Git hosting APIs behind one interface: GitHub, or Gitea for a fully local setup.

Only what Relay needs: list a branch's recent commits (optionally for one
file), show a commit's diff, read a file at a ref, and propose a change as a
draft pull request on a new branch. Nothing here can push to the base branch:
changes only ever arrive as pull requests, and the bot's token has no right to
merge (see scripts/deploy_repo.py for how the local repo is protected).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import quote

import httpx2


class HostError(RuntimeError):
    pass


@dataclass(frozen=True)
class Change:
    sha: str
    time: str
    author: str
    message: str
    files: list[str]
    url: str


@dataclass(frozen=True)
class FileVersion:
    path: str
    content: str
    blob_sha: str


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    branch: str
    draft: bool


class GitHost(Protocol):
    repo: str
    base: str

    async def changes(
        self, since: datetime, until: datetime, path: str | None, limit: int
    ) -> list[Change]: ...

    async def diff(self, sha: str) -> str: ...

    async def file(self, path: str, ref: str) -> FileVersion | None: ...

    async def parent(self, sha: str) -> str | None: ...

    async def find_pull(self, branch: str) -> PullRequest | None: ...

    async def commit_on_new_branch(
        self, *, branch: str, path: str, content: str, blob_sha: str, message: str
    ) -> None: ...

    async def open_draft_pull(self, *, branch: str, title: str, body: str) -> PullRequest: ...


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class _Rest:
    def __init__(self, http: httpx2.AsyncClient, repo: str, base: str):
        self.http = http
        self.repo = repo
        self.base = base

    async def _call(
        self, method: str, path: str, *, ok: tuple[int, ...] = (200, 201), **kwargs: Any
    ) -> httpx2.Response:
        try:
            response = await self.http.request(method, path, **kwargs)
        except httpx2.HTTPError as e:
            raise HostError(f"{type(e).__name__} calling {path}") from e
        if response.status_code not in ok:
            raise HostError(f"{method} {path}: HTTP {response.status_code}: {response.text[:200]}")
        return response


class Gitea(_Rest):
    """Gitea's REST API (v1). A "WIP:" title makes a pull request a draft."""

    def __init__(
        self, api_url: str, token: str, repo: str, base: str = "main", http: httpx2.AsyncClient | None = None
    ):
        super().__init__(
            http
            or httpx2.AsyncClient(
                base_url=api_url, headers={"Authorization": f"token {token}"}, timeout=15.0
            ),
            repo,
            base,
        )

    async def changes(self, since: datetime, until: datetime, path: str | None, limit: int) -> list[Change]:
        params: dict[str, Any] = {
            "sha": self.base,
            "since": _iso(since),
            "until": _iso(until),
            "limit": limit,
        }
        params |= {"files": "true", "stat": "false", "verification": "false"}
        if path:
            params["path"] = path
        response = await self._call("GET", f"/repos/{self.repo}/commits", params=params)
        return [
            Change(
                sha=c["sha"],
                time=c["commit"]["author"]["date"],
                author=c["commit"]["author"]["name"],
                message=c["commit"]["message"].strip(),
                files=[f["filename"] for f in c.get("files") or []],
                url=c["html_url"],
            )
            for c in response.json()
        ]

    async def diff(self, sha: str) -> str:
        return (await self._call("GET", f"/repos/{self.repo}/git/commits/{sha}.diff")).text

    async def file(self, path: str, ref: str) -> FileVersion | None:
        response = await self._call(
            "GET", f"/repos/{self.repo}/contents/{quote(path)}", params={"ref": ref}, ok=(200, 404)
        )
        if response.status_code == 404:
            return None
        data = response.json()
        return FileVersion(path, base64.b64decode(data["content"]).decode(), data["sha"])

    async def parent(self, sha: str) -> str | None:
        data = (await self._call("GET", f"/repos/{self.repo}/git/commits/{sha}")).json()
        parents = data.get("parents") or []
        return parents[0]["sha"] if parents else None

    async def find_pull(self, branch: str) -> PullRequest | None:
        response = await self._call("GET", f"/repos/{self.repo}/pulls/{self.base}/{branch}", ok=(200, 404))
        return self._pull(response.json()) if response.status_code == 200 else None

    async def commit_on_new_branch(
        self, *, branch: str, path: str, content: str, blob_sha: str, message: str
    ) -> None:
        await self._call(
            "PUT",
            f"/repos/{self.repo}/contents/{quote(path)}",
            json={
                "branch": self.base,
                "new_branch": branch,
                "content": _b64(content),
                "sha": blob_sha,
                "message": message,
            },
        )

    async def open_draft_pull(self, *, branch: str, title: str, body: str) -> PullRequest:
        response = await self._call(
            "POST",
            f"/repos/{self.repo}/pulls",
            json={"title": f"WIP: {title}", "head": branch, "base": self.base, "body": body},
        )
        return self._pull(response.json())

    @staticmethod
    def _pull(data: dict[str, Any]) -> PullRequest:
        return PullRequest(data["number"], data["html_url"], data["head"]["ref"], bool(data.get("draft")))


class GitHub(_Rest):
    """GitHub's REST API (2022-11-28)."""

    def __init__(
        self,
        token: str,
        repo: str,
        base: str = "main",
        api_url: str = "https://api.github.com",
        http: httpx2.AsyncClient | None = None,
    ):
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        super().__init__(
            http or httpx2.AsyncClient(base_url=api_url, headers=headers, timeout=15.0), repo, base
        )

    async def changes(self, since: datetime, until: datetime, path: str | None, limit: int) -> list[Change]:
        params: dict[str, Any] = {
            "sha": self.base,
            "since": _iso(since),
            "until": _iso(until),
            "per_page": limit,
        }
        if path:
            params["path"] = path
        listed = (await self._call("GET", f"/repos/{self.repo}/commits", params=params)).json()
        changes = []
        for c in listed:
            # The list endpoint has no file names; one call per commit adds them.
            detail = (await self._call("GET", f"/repos/{self.repo}/commits/{c['sha']}")).json()
            changes.append(
                Change(
                    sha=c["sha"],
                    time=c["commit"]["author"]["date"],
                    author=c["commit"]["author"]["name"],
                    message=c["commit"]["message"].strip(),
                    files=[f["filename"] for f in detail.get("files") or []],
                    url=c["html_url"],
                )
            )
        return changes

    async def diff(self, sha: str) -> str:
        response = await self._call(
            "GET", f"/repos/{self.repo}/commits/{sha}", headers={"Accept": "application/vnd.github.diff"}
        )
        return response.text

    async def file(self, path: str, ref: str) -> FileVersion | None:
        response = await self._call(
            "GET", f"/repos/{self.repo}/contents/{quote(path)}", params={"ref": ref}, ok=(200, 404)
        )
        if response.status_code == 404:
            return None
        data = response.json()
        return FileVersion(path, base64.b64decode(data["content"]).decode(), data["sha"])

    async def parent(self, sha: str) -> str | None:
        data = (await self._call("GET", f"/repos/{self.repo}/commits/{sha}")).json()
        parents = data.get("parents") or []
        return parents[0]["sha"] if parents else None

    async def find_pull(self, branch: str) -> PullRequest | None:
        owner = self.repo.split("/")[0]
        pulls = (
            await self._call(
                "GET", f"/repos/{self.repo}/pulls", params={"head": f"{owner}:{branch}", "state": "all"}
            )
        ).json()
        return self._pull(pulls[0]) if pulls else None

    async def commit_on_new_branch(
        self, *, branch: str, path: str, content: str, blob_sha: str, message: str
    ) -> None:
        head = (await self._call("GET", f"/repos/{self.repo}/git/ref/heads/{self.base}")).json()["object"][
            "sha"
        ]
        # 422: the branch exists already (a retry), which is fine.
        await self._call(
            "POST",
            f"/repos/{self.repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": head},
            ok=(201, 422),
        )
        await self._call(
            "PUT",
            f"/repos/{self.repo}/contents/{quote(path)}",
            json={"message": message, "content": _b64(content), "sha": blob_sha, "branch": branch},
        )

    async def open_draft_pull(self, *, branch: str, title: str, body: str) -> PullRequest:
        response = await self._call(
            "POST",
            f"/repos/{self.repo}/pulls",
            json={"title": title, "head": branch, "base": self.base, "body": body, "draft": True},
        )
        return self._pull(response.json())

    @staticmethod
    def _pull(data: dict[str, Any]) -> PullRequest:
        return PullRequest(data["number"], data["html_url"], data["head"]["ref"], bool(data.get("draft")))
