"""The postmortem writer: what it reads, what it publishes, and that it writes once."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from relay_agent.config import Settings
from relay_agent.graph.postmortem import Postmortem, facts
from relay_agent.llm.replay import ReplayProvider
from relay_agent.service.postmortems import IncidentResolved, PostmortemConsumer, Postmortems
from support import answer_turn
from test_contracts import errors

pytestmark = pytest.mark.anyio

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
RESOLVED = (CONTRACTS / "examples" / "incident-resolved.json").read_text(encoding="utf-8")
EXAMPLE_EVENTS = [
    json.loads(line)
    for line in (CONTRACTS / "examples" / "postmortem-events.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]
WRITTEN = EXAMPLE_EVENTS[1]["data"]["postmortem"]


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis(decode_responses=True)


def test_the_examples_match_the_contracts() -> None:
    assert errors("incident-resolved", json.loads(RESOLVED)) == []
    for event in EXAMPLE_EVENTS:
        assert errors("agent-event", event) == [], event["type"]
    assert IncidentResolved.model_validate_json(RESOLVED).incident["key"] == "INC-42"
    Postmortem.model_validate(WRITTEN)


def test_the_writer_reads_the_whole_record() -> None:
    text = facts(json.loads(RESOLVED)["incident"])
    assert "Incident INC-42" in text and "resolved 2026-10-04T09:20:00Z by bob" in text
    assert "HighErrorRate on orders (critical), resolved" in text
    assert "bad_deploy in orders, confidence 0.9" in text and "Reviewer: supported" in text
    assert "executed by alice (Matches the deploy timeline; roll back.)" in text
    assert "draft pull request http://localhost:3003/shop/deploy/pulls/3" in text
    assert "[approval.approved] alice approved" in text


async def published(redis: Any) -> list[dict[str, Any]]:
    return [json.loads(fields["event"]) for _, fields in await redis.xrange("relay.agent-events")]


async def test_a_resolved_incident_gets_one_postmortem(redis: Any) -> None:
    provider = ReplayProvider([answer_turn(WRITTEN)])
    handler = Postmortems(Settings(), redis, provider)
    message = IncidentResolved.model_validate_json(RESOLVED)
    await handler(message)
    await handler(message)  # redelivered: the lease says it's done

    events = await published(redis)
    assert [(e["type"], e["agent"], e["seq"]) for e in events] == [
        ("llm.completed", "postmortem", 0),
        ("postmortem.written", "postmortem", 1),
    ]
    assert events[0]["run_id"] == events[1]["run_id"] and events[0]["run_id"].startswith("pm-")
    data = events[1]["data"]
    assert data["postmortem"]["title"] == "orders 1.4.0 broke checkout"
    assert data["markdown"].startswith("# INC-42: orders 1.4.0 broke checkout")
    for event in events:
        assert errors("agent-event", event) == [], event["type"]
    assert len(provider.requests) == 1 and provider.requests[0].role == "postmortem"


async def test_an_unusable_answer_writes_nothing_and_can_be_retried(redis: Any) -> None:
    provider = ReplayProvider([answer_turn({"title": "missing everything else"}), answer_turn(WRITTEN)])
    handler = Postmortems(Settings(), redis, provider)
    message = IncidentResolved.model_validate_json(RESOLVED)
    await handler(message)
    assert [e["type"] for e in await published(redis)] == ["llm.completed"]
    await handler(message)  # the lease was released: a later delivery tries again
    assert [e["type"] for e in await published(redis)][-1] == "postmortem.written"


async def test_malformed_messages_are_dead_lettered(redis: Any) -> None:
    consumer = PostmortemConsumer(redis, Settings(), ReplayProvider([]), "worker-1", block_ms=10)
    await consumer.ensure_group()
    await redis.xadd("relay.resolved", {"event": json.dumps({"type": "incident.opened"})})
    assert await consumer.read() == 1
    [(_, dead)] = await redis.xrange("relay.resolved.dead")
    assert dead["reason"].startswith("malformed incident.resolved")
