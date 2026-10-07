"""The fixer: what it proposes, that it waits for a human, and what it runs after."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import anyio
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from relay_agent.graph.fixer import Fixer, reverse_diff, thread
from relay_agent.graph.schemas import Incident
from relay_agent.tools.toolbox import ToolOutcome
from support import spec

INCIDENT = Incident(
    id="5d0c7a0e-0000-4000-8000-000000000001",
    number=12,
    title="HighErrorRate on orders",
    opened_at="2026-10-05T10:00:00Z",
    alerts=[],
)
BAD = {
    "sha": "a" * 40,
    "time": "2026-10-05T09:58:00Z",
    "author": "ci-bot",
    "message": "orders 1.4.0: support stacked discount codes (#212)",
    "files": ["services/orders.yaml"],
}
ROLLBACK = {**BAD, "sha": "b" * 40, "time": "2026-10-05T10:01:00Z", "message": "Rollback orders to 1.3.0"}
DIFF = (
    "diff --git a/services/orders.yaml b/services/orders.yaml\n"
    "index 72c75fa..25de7b7 100644\n--- a/services/orders.yaml\n+++ b/services/orders.yaml\n"
    "@@ -1,3 +1,3 @@\n service: orders\n-image: relay/sandbox-orders:1.3.0\n+image: relay/sandbox-orders:1.4.0\n env:\n"
)
PR = {
    "pull_request": 3,
    "url": "http://localhost:3003/shop/deploy/pulls/3",
    "branch": "relay/inc-12-x",
    "created": True,
}


def report(category: str = "bad_deploy", confidence: float = 0.85) -> dict[str, Any]:
    return {
        "summary": "orders 1.4.0 broke checkout",
        "hypotheses": [
            {
                "category": category,
                "service": "orders",
                "component": "orders 1.4.0",
                "confidence": confidence,
                "summary": "orders 1.4.0 throws NullPointerException when no discount code is given",
                "evidence": [{"evidence_id": "E2", "quote": "NullPointerException", "shows": "the crash"}],
                "suggested_fix": "roll back",
            }
        ],
    }


class Repo:
    """The github MCP tools over a list of changes (newest first)."""

    def __init__(self, changes: list[dict[str, Any]]):
        self.changes = changes
        self.specs = [
            spec("github__recent_changes"),
            spec("github__change_diff"),
            spec("github__open_draft_pr", read_only=False),
        ]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, arguments))
        if name == "github__recent_changes":
            end = (
                datetime.fromisoformat(arguments["end"].replace("Z", "+00:00"))
                if "end" in arguments
                else datetime.now(UTC)
            )
            listed = [
                c for c in self.changes if datetime.fromisoformat(c["time"].replace("Z", "+00:00")) <= end
            ]
            return ToolOutcome(json.dumps({"repo": "shop/deploy", "changes": listed}))
        if name == "github__change_diff":
            return ToolOutcome(json.dumps({"sha": arguments["sha"], "diff": DIFF}))
        return ToolOutcome(json.dumps(PR))


class Sink:
    def __init__(self) -> None:
        self.seq = 0
        self.events: list[tuple[int, str, dict[str, Any]]] = []

    async def emit(self, kind: str, /, **data: Any) -> None:
        self.events.append((self.seq, kind, data))
        self.seq += 1


def started(
    repo: Repo, saver: InMemorySaver, rep: dict[str, Any] | None = None
) -> tuple[dict[str, Any], Sink]:
    sink = Sink()
    state = anyio.run(lambda: Fixer(repo, sink).start(saver, INCIDENT, "run-1", rep or report(), seq=12))
    return state, sink


def test_a_bad_deploy_becomes_an_approval_request_and_the_run_waits() -> None:
    saver = InMemorySaver()
    state, sink = started(Repo([BAD]), saver)
    [(seq, kind, data)] = sink.events
    assert (seq, kind) == (12, "approval.requested")  # the run's sequence continues
    assert data["title"] == 'Revert "orders 1.4.0: support stacked discount codes (#212)"'
    assert data["risk"] == "low"
    assert "-image: relay/sandbox-orders:1.4.0\n+image: relay/sandbox-orders:1.3.0" in data["diff"]
    action = json.loads(data["action"])
    assert action | {"body": ""} == {
        "kind": "revert_change",
        "repo": "shop/deploy",
        "commit": "a" * 40,
        "path": "services/orders.yaml",
        "title": data["title"],
        "body": "",
        "incident": "INC-12",
    }
    assert "NullPointerException" in action["body"] and "E2" in action["body"]
    assert state["seq"] == 13
    snapshot = anyio.run(lambda: Fixer(Repo([]), Sink()).graph(saver).aget_state(thread("run-1")))
    assert snapshot.next == ("gate",)


def test_an_approval_opens_the_pull_request_with_the_exact_action_and_token() -> None:
    saver = InMemorySaver()
    repo = Repo([BAD])
    _, first = started(repo, saver)
    action = first.events[0][2]["action"]
    # The decision arrives later, in another process: a new Fixer, the same checkpoint store.
    sink = Sink()
    decision = {"decision": "approved", "decided_by": "alice", "action": action, "token": "signed.jwt.token"}
    state = anyio.run(lambda: Fixer(repo, sink).resume(saver, "run-1", decision))
    assert repo.calls[-1] == (
        "github__open_draft_pr",
        {"action": action, "approval_token": "signed.jwt.token"},
    )
    [(seq, kind, data)] = sink.events
    assert (seq, kind, data["result"]) == (13, "action.executed", PR)
    assert state["outcome"]["status"] == "executed"
    assert anyio.run(lambda: Fixer(repo, Sink()).resume(saver, "run-1", decision)) is None  # applied once


def test_a_rejection_runs_nothing() -> None:
    saver = InMemorySaver()
    repo = Repo([BAD])
    started(repo, saver)
    sink = Sink()
    decision = {"decision": "rejected", "decided_by": "alice", "reason": "not now", "action": "x"}
    state = anyio.run(lambda: Fixer(repo, sink).resume(saver, "run-1", decision))
    assert state["outcome"] == {"status": "rejected", "reason": "not now"}
    assert sink.events == []
    assert all(name != "github__open_draft_pr" for name, _ in repo.calls)


def test_an_approved_action_that_is_not_the_proposal_is_refused() -> None:
    saver = InMemorySaver()
    repo = Repo([BAD])
    _, first = started(repo, saver)
    tampered = first.events[0][2]["action"].replace("a" * 40, "c" * 40)
    sink = Sink()
    decision = {"decision": "approved", "decided_by": "alice", "action": tampered, "token": "t"}
    state = anyio.run(lambda: Fixer(repo, sink).resume(saver, "run-1", decision))
    assert sink.events[0][1] == "action.failed"
    assert state["outcome"]["status"] == "failed"
    assert all(name != "github__open_draft_pr" for name, _ in repo.calls)


@pytest.mark.parametrize(
    ("changes", "rep", "reason"),
    [
        ([BAD], report("db_pool_exhaustion"), "not fixed by undoing a change"),
        ([BAD], report(confidence=0.3), "below"),
        ([], report(), "no change to orders"),
        # Already undone (the change after the incident is a rollback): proposing would redo the bad deploy.
        ([ROLLBACK, BAD], report(), "changed again"),
        # An earlier drill's rollback is the last change before this incident: never undo an undo.
        ([{**ROLLBACK, "time": "2026-10-05T09:30:00Z"}], report(), "itself an undo"),
    ],
    ids=["category", "confidence", "no-change", "superseded", "undo"],
)
def test_no_proposal_when_undoing_a_change_is_not_the_fix(changes, rep, reason) -> None:
    state, sink = started(Repo(changes), InMemorySaver(), rep)
    assert sink.events == []
    assert reason in state["skipped"]


def test_nothing_waits_for_a_decision_about_an_unknown_run() -> None:
    decision = {"decision": "approved", "decided_by": "alice", "action": "{}", "token": "t"}
    assert anyio.run(lambda: Fixer(Repo([]), Sink()).resume(InMemorySaver(), "run-404", decision)) is None


def test_reverse_diff_undoes_a_change() -> None:
    assert reverse_diff(DIFF) == (
        "diff --git a/services/orders.yaml b/services/orders.yaml\n"
        "index 25de7b7..72c75fa 100644\n--- a/services/orders.yaml\n+++ b/services/orders.yaml\n"
        "@@ -1,3 +1,3 @@\n service: orders\n-image: relay/sandbox-orders:1.4.0\n+image: relay/sandbox-orders:1.3.0\n env:\n"
    )


def test_the_lookback_ends_when_the_incident_opened() -> None:
    repo = Repo([BAD])
    started(repo, InMemorySaver())
    first_end = repo.calls[0][1]["end"]
    assert datetime.fromisoformat(first_end.replace("Z", "+00:00")) == datetime(
        2026, 10, 5, 10, 0, tzinfo=UTC
    )
