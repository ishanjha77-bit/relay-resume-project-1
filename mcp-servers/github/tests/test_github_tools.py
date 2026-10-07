"""The deploy-repo tools against an in-memory Gitea, with real RS256 approval tokens."""

from __future__ import annotations

import json
import time
from typing import Any

import anyio
import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from gitea_fake import REPO, FakeGitea
from github_mcp.approval import ApprovalVerifier, action_digest
from github_mcp.hosts import Gitea
from github_mcp.server import branch_for, build_server
from relay_mcp_kit import KitSettings

ORDERS_GOOD = "service: orders\nimage: relay/sandbox-orders:1.3.0\nenv:\n  DB_URL: jdbc:postgresql://postgres:5432/orders\n"
ORDERS_BAD = ORDERS_GOOD.replace("1.3.0", "1.4.0")
GATEWAY = 'service: gateway\nimage: relay/sandbox-gateway:2.1.0\nenv:\n  UPSTREAM_TIMEOUT_MS: "4000"\n'

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def repo() -> FakeGitea:
    fake = FakeGitea()
    fake.commit(
        "main",
        {"services/orders.yaml": ORDERS_GOOD, "services/gateway.yaml": GATEWAY},
        "Seed the deploy repo",
    )
    fake.commit(
        "main",
        {"services/orders.yaml": ORDERS_BAD},
        "orders 1.4.0: support stacked discount codes (#212)",
        minutes=30,
    )
    return fake


def token(
    action: str,
    *,
    key=KEY,
    issuer: str = "relay-platform",
    audience: str = "relay-actions",
    ttl: int = 600,
    approval: str = "6f1c2d3e-aaaa-bbbb-cccc-000000000001",
) -> str:
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": approval,
        "iat": now,
        "exp": now + ttl,
        "action_sha256": action_digest(action),
        "approved_by": "vic",
        "incident": "INC-12",
    }
    return jwt.encode(claims, key, algorithm="RS256")


def revert(fake: FakeGitea, sha: str | None = None) -> str:
    sha = sha or fake.branches["main"]
    return json.dumps(
        {
            "kind": "revert_change",
            "repo": REPO,
            "commit": sha,
            "path": "services/orders.yaml",
            "title": "Roll back orders to 1.3.0",
            "body": "Relay INC-12: orders 1.4.0 throws NullPointerException.",
            "incident": "INC-12",
        }
    )


def call(fake: FakeGitea, calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    http = httpx2.AsyncClient(base_url="http://gitea/api/v1", transport=httpx2.MockTransport(fake.handler))
    host = Gitea("http://gitea/api/v1", "t", REPO, http=http)
    verifier = ApprovalVerifier(lambda _token: KEY.public_key(), "relay-platform")
    mcp = build_server(host, verifier, KitSettings(token="test"))

    async def run() -> list[dict[str, Any]]:
        out = []
        for tool, args in calls:
            result = await mcp.call_tool(tool, args)
            out.append(json.loads(result.content[0].text))
        return out

    return anyio.run(run)


def test_recent_changes_show_what_changed_when_and_why(repo: FakeGitea) -> None:
    [everything, orders, gateway] = call(
        repo,
        [
            ("recent_changes", {"minutes": 120, "end": "2026-10-05T10:00:00Z"}),
            ("recent_changes", {"minutes": 120, "service": "orders", "end": "2026-10-05T10:00:00Z"}),
            ("recent_changes", {"minutes": 120, "service": "gateway", "end": "2026-10-05T10:00:00Z"}),
        ],
    )
    assert [c["message"] for c in everything["changes"]] == [
        "orders 1.4.0: support stacked discount codes (#212)",
        "Seed the deploy repo",
    ]
    top = orders["changes"][0]
    assert (top["author"], top["files"], top["time"]) == (
        "ci-bot",
        ["services/orders.yaml"],
        "2026-10-05T09:30:00Z",
    )
    assert [c["message"] for c in gateway["changes"]] == ["Seed the deploy repo"]


def test_a_change_a_later_revert_undid_says_so(repo: FakeGitea) -> None:
    tight = GATEWAY.replace('UPSTREAM_TIMEOUT_MS: "4000"', 'UPSTREAM_TIMEOUT_MS: "80"')
    assert tight != GATEWAY
    repo.commit(
        "main", {"services/gateway.yaml": tight}, "gateway: cut the upstream timeout (#219)", minutes=40
    )
    revert_sha = repo.commit(
        "main", {"services/gateway.yaml": GATEWAY}, "Revert UPSTREAM_TIMEOUT_MS on gateway", minutes=50
    )
    [listed] = call(repo, [("recent_changes", {"minutes": 120, "end": "2026-10-05T10:00:00Z"})])

    by_message = {c["message"]: c for c in listed["changes"]}
    cut = by_message["gateway: cut the upstream timeout (#219)"]
    assert cut["undone_by"] == {
        "sha": revert_sha,
        "time": "2026-10-05T09:50:00Z",
        "message": "Revert UPSTREAM_TIMEOUT_MS on gateway",
    }
    # Still in effect: the orders release, and the revert itself.
    assert "undone_by" not in by_message["orders 1.4.0: support stacked discount codes (#212)"]
    assert "undone_by" not in by_message["Revert UPSTREAM_TIMEOUT_MS on gateway"]


def test_a_change_diff_shows_the_exact_lines(repo: FakeGitea) -> None:
    [out] = call(repo, [("change_diff", {"sha": repo.branches["main"]})])
    assert "-image: relay/sandbox-orders:1.3.0" in out["diff"]
    assert "+image: relay/sandbox-orders:1.4.0" in out["diff"]


def test_read_file_at_main_and_at_a_commit(repo: FakeGitea) -> None:
    seed = repo.commits[repo.branches["main"]].parent
    now, before, missing = call(
        repo,
        [
            ("read_file", {"path": "services/orders.yaml"}),
            ("read_file", {"path": "services/orders.yaml", "ref": seed}),
            ("read_file", {"path": "services/nope.yaml"}),
        ],
    )
    assert "1.4.0" in now["content"] and "1.3.0" in before["content"]
    assert "no file" in missing["error"]


def test_an_approved_revert_opens_one_draft_pull_request(repo: FakeGitea) -> None:
    action = revert(repo)
    first, retry = call(
        repo,
        [
            ("open_draft_pr", {"action": action, "approval_token": token(action)}),
            ("open_draft_pr", {"action": action, "approval_token": token(action)}),
        ],
    )
    branch = branch_for("6f1c2d3e-aaaa-bbbb-cccc-000000000001", "INC-12")
    assert first == {
        "pull_request": 1,
        "url": f"http://gitea:3000/{REPO}/pulls/1",
        "branch": branch,
        "created": True,
        "draft": True,
    }
    assert retry["created"] is False and retry["pull_request"] == 1
    assert len(repo.pulls) == 1
    assert repo.pulls[0]["title"] == "WIP: Roll back orders to 1.3.0"
    assert repo.resolve(branch).tree["services/orders.yaml"] == ORDERS_GOOD  # the change, undone
    assert repo.resolve("main").tree["services/orders.yaml"] == ORDERS_BAD  # main untouched


@pytest.mark.parametrize(
    "make_token",
    [
        lambda action: token(action.replace("1.3.0", "1.2.0")),  # approved something else
        lambda action: token(action, key=OTHER_KEY),  # not the platform's key
        lambda action: token(action, ttl=-60),  # expired
        lambda action: token(action, issuer="someone-else"),
        lambda action: token(action, audience="relay-console"),
        lambda action: "not-a-jwt",
    ],
    ids=["other-action", "other-key", "expired", "issuer", "audience", "garbage"],
)
def test_writes_without_a_matching_approval_are_denied(repo: FakeGitea, make_token) -> None:
    action = revert(repo)
    [out] = call(repo, [("open_draft_pr", {"action": action, "approval_token": make_token(action)})])
    assert out["denied"] is True
    assert repo.pulls == [] and set(repo.branches) == {"main"}


def test_a_revert_refuses_to_undo_newer_work(repo: FakeGitea) -> None:
    target = repo.branches["main"]
    repo.commit(
        "main",
        {"services/orders.yaml": ORDERS_BAD + '  FEATURE_X: "on"\n'},
        "orders: enable feature X",
        minutes=40,
    )
    action = revert(repo, target)
    [out] = call(repo, [("open_draft_pr", {"action": action, "approval_token": token(action)})])
    assert "changed again" in out["error"]
    assert repo.pulls == []


def test_actions_for_another_repository_are_denied(repo: FakeGitea) -> None:
    action = revert(repo).replace(REPO, "shop/billing")
    [out] = call(repo, [("open_draft_pr", {"action": action, "approval_token": token(action)})])
    assert out["denied"] is True and "shop/billing" in out["error"]


def test_the_write_tool_is_marked_as_one(repo: FakeGitea) -> None:
    http = httpx2.AsyncClient(base_url="http://gitea/api/v1", transport=httpx2.MockTransport(repo.handler))
    mcp = build_server(
        Gitea("http://gitea/api/v1", "t", REPO, http=http),
        ApprovalVerifier(lambda _t: KEY.public_key(), "relay-platform"),
        KitSettings(token="test"),
    )
    tools = {t.name: t.annotations for t in anyio.run(mcp.list_tools)}
    assert tools["open_draft_pr"].read_only_hint is False
    assert all(tools[name].read_only_hint for name in ("recent_changes", "change_diff", "read_file"))
