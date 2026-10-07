"""Approval decisions: the consumer, the contract, and a run that pauses for a human and resumes."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from relay_agent.config import Settings, Streams
from relay_agent.graph.schemas import Incident
from relay_agent.service.approvals import ApprovalConsumer, ApprovalDecision
from relay_agent.service.runner import Runner
from support import answer_turn, make_investigator, tool_turn
from support import report as full_report
from test_contracts import errors
from test_fixer import BAD, Repo
from test_fixer import report as bad_deploy_report

pytestmark = pytest.mark.anyio

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
EXAMPLE = (CONTRACTS / "examples" / "approval-decision.json").read_text(encoding="utf-8")
STREAM, GROUP, DEAD = "relay.approvals", "agent-service", "relay.approvals.dead"
INCIDENT = Incident(
    id="5d0c7a0e-0000-4000-8000-000000000001",
    number=12,
    title="HighErrorRate on orders",
    opened_at="2026-10-05T10:00:00Z",
    alerts=[],
)


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis(decode_responses=True)


# ---------------------------------------------------------------- contract
def test_the_decision_example_matches_the_contract_and_parses() -> None:
    assert errors("approval-decision", json.loads(EXAMPLE)) == []
    decision = ApprovalDecision.model_validate_json(EXAMPLE)
    assert (decision.decision, decision.decided_by, decision.run_id) == ("approved", "alice", "run-example")


def test_the_approval_event_examples_match_the_contract() -> None:
    lines = (CONTRACTS / "examples" / "approval-events.jsonl").read_text(encoding="utf-8").splitlines()
    for event in (json.loads(line) for line in lines if line.strip()):
        assert errors("agent-event", event) == [], event["type"]


def test_the_review_event_examples_match_the_contract() -> None:
    lines = (CONTRACTS / "examples" / "review-events.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    assert [e["type"] for e in events] == ["llm.completed", "review.completed", "run.finished"]
    for event in events:
        assert errors("agent-event", event) == [], event["type"]


# ---------------------------------------------------------------- consumer
async def test_each_decision_resumes_its_run_and_is_acknowledged(redis: Any) -> None:
    resumed: list[ApprovalDecision] = []

    async def resume(decision: ApprovalDecision) -> None:
        resumed.append(decision)

    consumer = ApprovalConsumer(redis, Streams(), resume, "worker-1", block_ms=10)
    await consumer.ensure_group()
    await redis.xadd(STREAM, {"event": EXAMPLE})
    await redis.xadd(STREAM, {"event": "{not json"})
    assert await consumer.read() == 2

    assert [d.approval_id for d in resumed] == ["9b2e4c1a-5d6f-4a7b-8c9d-0e1f2a3b4c5d"]
    assert (await redis.xpending(STREAM, GROUP))["pending"] == 0
    [(_, dead)] = await redis.xrange(DEAD)
    assert dead["reason"].startswith("malformed approval decision")


async def test_a_resume_that_crashes_is_still_acknowledged(redis: Any) -> None:
    async def resume(decision: ApprovalDecision) -> None:
        raise RuntimeError("the fixer reports its own failures")

    consumer = ApprovalConsumer(redis, Streams(), resume, "worker-1", block_ms=10)
    await consumer.ensure_group()
    await redis.xadd(STREAM, {"event": EXAMPLE})
    await consumer.read()
    assert (await redis.xpending(STREAM, GROUP))["pending"] == 0


# ------------------------------------------------------- pause and resume
def runner(redis: Any, repo: Repo, saver: InMemorySaver | None) -> Runner:
    @asynccontextmanager
    async def toolbox():
        yield repo

    async def checkpoints() -> InMemorySaver | None:
        return saver

    return Runner(Settings(), redis, provider=None, toolbox=toolbox, checkpoints=checkpoints)


async def published(redis: Any) -> list[dict[str, Any]]:
    return [json.loads(fields["event"]) for _, fields in await redis.xrange("relay.agent-events")]


async def test_a_concluded_bad_deploy_waits_for_approval_then_opens_the_pull_request(redis: Any) -> None:
    repo, saver = Repo([BAD]), InMemorySaver()
    service = runner(redis, repo, saver)
    verdict = full_report(quote="orders 1.4.0: support stacked discount codes (#212)")
    verdict["hypotheses"][0] |= {"category": "bad_deploy", "service": "orders", "confidence": 0.9}
    investigator, _, _ = make_investigator(
        [tool_turn(("github__recent_changes", {"service": "orders"})), answer_turn(verdict)], repo
    )
    state = await investigator.run(INCIDENT, run_id="run-1")
    stream = service._stream("run-1", INCIDENT.id)
    stream.seq = 9
    await service._propose_fix(INCIDENT, "run-1", state, repo, stream, {})
    await stream.emit("run.finished", status="concluded")  # as a run does once the fixer pauses

    [requested, finished] = await published(redis)
    assert (requested["type"], requested["agent"], requested["seq"]) == ("approval.requested", "fixer", 9)
    assert (finished["type"], finished["seq"]) == ("run.finished", 10)
    assert errors("agent-event", requested) == []
    data = requested["data"]

    decision = ApprovalDecision(
        approval_id=data["approval_id"],
        incident_id=INCIDENT.id,
        run_id="run-1",
        decision="approved",
        decided_by="alice",
        decided_at="2026-10-05T10:05:00Z",
        action=data["action"],
        token="signed.jwt.token",
    )
    await service.resume(decision)
    executed = (await published(redis))[-1]
    # A seq of its own: platform-api keeps one event per (run_id, seq), and run.finished took 10.
    assert executed["type"] == "action.executed" and executed["seq"] > finished["seq"]
    assert errors("agent-event", executed) == []
    assert repo.calls[-1] == (
        "github__open_draft_pr",
        {"action": data["action"], "approval_token": "signed.jwt.token"},
    )

    await service.resume(decision)  # redelivered: applied once, nothing new published
    assert len(await published(redis)) == 3


async def test_an_approval_for_a_run_that_stopped_waiting_is_reported_not_run(redis: Any) -> None:
    repo = Repo([BAD])
    service = runner(redis, repo, InMemorySaver())  # an empty store: the checkpoint expired
    decision = ApprovalDecision.model_validate_json(EXAMPLE)
    await service.resume(decision)
    [failed] = await published(redis)
    assert failed["type"] == "action.failed"
    assert "stopped waiting" in failed["data"]["error"]
    assert errors("agent-event", failed) == []
    assert all(name != "github__open_draft_pr" for name, _ in repo.calls)


async def test_no_checkpoint_store_means_no_proposal(redis: Any) -> None:
    repo = Repo([BAD])
    service = runner(redis, repo, None)
    stream = service._stream("run-2", INCIDENT.id)
    await service._propose_fix(
        INCIDENT, "run-2", {"status": "concluded", "report": bad_deploy_report()}, repo, stream, {}
    )
    assert await published(redis) == []
