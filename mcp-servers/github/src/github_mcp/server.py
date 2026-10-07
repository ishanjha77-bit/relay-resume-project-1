"""MCP server: the deploy repo's history for the agents, and approval-gated draft PRs.

The deploy repo holds the desired state of every sandbox service (image and
environment), one file per service; CD commits each rollout there. Three read
tools let an investigation see what changed and when. One write tool,
``open_draft_pr``, proposes reverting a change, and runs only with an approval
token the platform signs when a human approves that exact action.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from typing import Literal

import anyio
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ValidationError
from relay_mcp_kit import KitSettings, clip, create_server, redact, serve, to_json

from github_mcp.approval import ApprovalError, ApprovalVerifier
from github_mcp.hosts import Change, Gitea, GitHost, GitHub, HostError

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False, idempotent_hint=True)
# Additive (a branch and a draft PR, nothing merged) and idempotent per approval.
PROPOSE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)
MAX_TEXT = 8_000
MAX_CHANGES = 20
# Messages of commits that undo an earlier change: a revert, or a rollback to an older version.
UNDO = re.compile(r"^(revert|rollback|roll back)\b", re.IGNORECASE)

INSTRUCTIONS = """\
The deploy repository: one file per sandbox service (services/<name>.yaml) with
the image and environment that CD rolls out; every rollout is a commit whose
message says why. recent_changes lists what changed in a window (for one
service or all), change_diff shows exactly what a change did, read_file reads
a file at a ref. A change shortly before a problem started is a strong lead;
one after it is not a cause, and neither is one undone (undone_by) before it."""


def undone(changes: list[Change]) -> dict[str, Change]:
    """Which of `changes` (newest first) a later revert or rollback undid: for each
    undo, the latest earlier change to each file it touched that isn't an undo itself."""
    by: dict[str, Change] = {}
    for i, undo in enumerate(changes):
        if not UNDO.match(undo.message):
            continue
        for path in undo.files:
            earlier = next(
                (
                    c
                    for c in changes[i + 1 :]
                    if path in c.files and not UNDO.match(c.message) and c.sha not in by
                ),
                None,
            )
            if earlier is not None:
                by[earlier.sha] = undo
    return by


class RevertChange(BaseModel):
    """Propose undoing one recorded change: the file goes back to its content
    before that commit, on a new branch, as a draft pull request."""

    kind: Literal["revert_change"]
    repo: str
    commit: str
    path: str
    title: str
    body: str
    incident: str = ""


def parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def branch_for(approval_id: str, incident: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{incident}-{approval_id[:8]}".lower()).strip("-")
    return f"relay/{slug}"


def build_server(host: GitHost, verifier: ApprovalVerifier, settings: KitSettings | None = None) -> MCPServer:
    mcp = create_server(
        "github", instructions=INSTRUCTIONS, scopes=["deploys:read", "deploys:propose"], settings=settings
    )

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def recent_changes(minutes: int = 180, service: str | None = None, end: str | None = None) -> str:
        """Commits to the deploy repository in a time window, newest first: what
        changed (files), when, by whom, and the commit message (often a PR title).
        A change that a later revert or rollback undid carries `undone_by`: from
        that time on, it no longer applies.

        Args:
            minutes: How far back from `end` to look (default 180).
            service: Only changes to this service's file (e.g. "orders"); omit for all.
            end: ISO-8601 end of the window; default now.
        """
        try:
            until = parse_time(end)
            since = until - timedelta(minutes=max(1, minutes))
            path = f"services/{service}.yaml" if service else None
            changes = await host.changes(since, until, path, MAX_CHANGES)
        except (HostError, ValueError) as e:
            return to_json({"error": str(e)})
        undone_by = undone(changes)
        return to_json(
            {
                "repo": host.repo,
                "branch": host.base,
                "window": {"since": since.isoformat(), "until": until.isoformat()},
                "changes": [
                    {
                        "sha": c.sha,
                        "time": c.time,
                        "author": c.author,
                        "message": redact(c.message),
                        "files": c.files,
                        **(
                            {"undone_by": {"sha": u.sha, "time": u.time, "message": redact(u.message)}}
                            if (u := undone_by.get(c.sha))
                            else {}
                        ),
                    }
                    for c in changes
                ],
            }
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def change_diff(sha: str) -> str:
        """The unified diff of one change: exactly which lines of which files it changed.

        Args:
            sha: The commit SHA, as listed by recent_changes.
        """
        try:
            diff = await host.diff(sha.strip())
        except HostError as e:
            return to_json({"error": str(e)})
        return to_json({"sha": sha.strip(), "diff": clip(redact(diff), MAX_TEXT)})

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def read_file(path: str, ref: str | None = None) -> str:
        """A file of the deploy repository, e.g. services/orders.yaml.

        Args:
            path: Path in the repository.
            ref: A branch or commit SHA; default the main branch.
        """
        try:
            version = await host.file(path.strip(), ref or host.base)
        except HostError as e:
            return to_json({"error": str(e)})
        if version is None:
            return to_json({"error": f"no file {path!r} at {ref or host.base}"})
        return to_json(
            {
                "path": version.path,
                "ref": ref or host.base,
                "content": clip(redact(version.content), MAX_TEXT),
            }
        )

    @mcp.tool(annotations=PROPOSE, structured_output=False)
    async def open_draft_pr(action: str, approval_token: str) -> str:
        """Open a draft pull request that reverts one change. Needs an approval token
        from the platform, issued when a human approved this exact action; nothing
        is merged.

        Args:
            action: The approved action, as JSON text, byte for byte as approved.
            approval_token: The platform's signed approval for it.
        """
        try:
            approval = await anyio.to_thread.run_sync(verifier.verify, approval_token, action)
            change = RevertChange.model_validate_json(action)
        except ApprovalError as e:
            return to_json({"error": str(e), "denied": True})
        except ValidationError as e:
            return to_json({"error": f"not a valid action: {e.error_count()} problems"})
        if change.repo != host.repo:
            return to_json({"error": f"this server manages {host.repo}, not {change.repo}", "denied": True})
        try:
            return to_json(await _revert(host, change, branch_for(approval.id, change.incident)))
        except HostError as e:
            return to_json({"error": str(e)})

    return mcp


async def _revert(host: GitHost, change: RevertChange, branch: str) -> dict[str, object]:
    existing = await host.find_pull(branch)
    if existing:  # a retry after the PR was opened: same answer, no second PR
        return {"pull_request": existing.number, "url": existing.url, "branch": branch, "created": False}
    current = await host.file(change.path, host.base)
    at_commit = await host.file(change.path, change.commit)
    if current is None or at_commit is None:
        raise HostError(f"{change.path} does not exist on {host.base} or at {change.commit[:10]}")
    if current.content != at_commit.content:
        raise HostError(
            f"{change.path} changed again after {change.commit[:10]}; the revert would undo newer work"
        )
    parent = await host.parent(change.commit)
    before = await host.file(change.path, parent) if parent else None
    if before is None:
        raise HostError(f"{change.commit[:10]} created {change.path}; there is no earlier version to restore")
    await host.commit_on_new_branch(
        branch=branch,
        path=change.path,
        content=before.content,
        blob_sha=current.blob_sha,
        message=f"Revert {change.commit[:10]}: {change.title}",
    )
    pull = await host.open_draft_pull(branch=branch, title=change.title, body=change.body)
    return {
        "pull_request": pull.number,
        "url": pull.url,
        "branch": branch,
        "created": True,
        "draft": pull.draft,
    }


def host_from_env() -> GitHost:
    kind = os.environ.get("GIT_HOST", "gitea")
    token = os.environ.get("GIT_TOKEN", "")  # empty until `make deploy-repo` creates the bot's token
    repo = os.environ.get("GIT_REPO", "shop/deploy")
    base = os.environ.get("GIT_BASE_BRANCH", "main")
    if kind == "github":
        return GitHub(token, repo, base, api_url=os.environ.get("GIT_API_URL", "https://api.github.com"))
    return Gitea(os.environ.get("GIT_API_URL", "http://gitea:3000/api/v1"), token, repo, base)


def main() -> None:
    settings = KitSettings.from_env()
    verifier = ApprovalVerifier.from_jwks(
        os.environ.get("APPROVAL_JWKS_URL", "http://platform-api:8080/.well-known/jwks.json"),
        os.environ.get("APPROVAL_ISSUER", "relay-platform"),
    )
    serve(build_server(host_from_env(), verifier, settings), settings)


if __name__ == "__main__":
    main()
