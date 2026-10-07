"""The Java/Python boundary: what agent-service consumes and publishes matches
contracts/schemas, the same files platform-api's tests check."""

import json
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from jsonschema import Draft202012Validator
from redis.exceptions import ConnectionError as RedisConnectionError
from relay_agent.events import StreamSink
from relay_agent.service.consumer import IncidentOpened
from support import INCIDENT, FakeToolbox, answer_turn, make_investigator, report, tool_turn

pytestmark = pytest.mark.anyio

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
EXAMPLE_INCIDENT = "4f1c2a9e-7b3d-4e8a-9c1f-2d6b8e0a5c31"


def errors(schema: str, document: dict[str, Any]) -> list[str]:
    spec = json.loads((CONTRACTS / "schemas" / f"{schema}.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(spec, format_checker=Draft202012Validator.FORMAT_CHECKER)
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in validator.iter_errors(document)]


def example_events() -> list[dict[str, Any]]:
    lines = (CONTRACTS / "examples" / "agent-events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_example_agent_events_are_valid() -> None:
    events = example_events()
    assert [e["seq"] for e in events] == list(range(len(events)))
    for event in events:
        assert errors("agent-event", event) == [], event["type"]


def test_example_incident_opened_is_valid_and_parses() -> None:
    document = json.loads((CONTRACTS / "examples" / "incident-opened.json").read_text(encoding="utf-8"))
    assert errors("incident-opened", document) == []
    incident = IncidentOpened.model_validate(document).incident
    assert (incident.id, incident.number, len(incident.alerts)) == (EXAMPLE_INCIDENT, 42, 2)


def test_the_schemas_reject_what_they_should() -> None:
    started, tool_called = example_events()[0], example_events()[3]
    assert errors("agent-event", {**started, "seq": -1})
    assert errors("agent-event", {**started, "incident_id": "INC-20261004"})  # not a uuid
    assert errors("agent-event", {**started, "at": "yesterday"})  # not a date-time
    assert errors("agent-event", {**started, "type": "tool.called"})  # data of another type
    assert errors("agent-event", {**tool_called, "data": {**tool_called["data"], "evidence_id": "X1"}})
    assert errors("agent-event", {**started, "surprise": True})


async def test_a_published_investigation_matches_the_contract(toolbox: FakeToolbox) -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    incident = INCIDENT.model_copy(update={"id": EXAMPLE_INCIDENT, "number": 42})
    investigator, _, _ = make_investigator(
        [
            tool_turn(("logs__error_summary", {"minutes": 15}), ("metrics__service_health", {})),
            answer_turn(report()),
        ],
        toolbox,
    )
    investigator.events = StreamSink(
        redis, "relay.agent-events", run_id="run-contract", incident_id=incident.id
    )

    state = await investigator.run(incident, run_id="run-contract")
    events = [json.loads(fields["event"]) for _, fields in await redis.xrange("relay.agent-events")]

    assert state["status"] == "concluded"
    for event in events:
        assert errors("agent-event", event) == [], event["type"]
    assert [e["type"] for e in events] == [
        "run.started",
        "agent.progress",
        "llm.completed",
        "tool.called",
        "tool.called",
        "llm.completed",
        "investigation.concluded",
    ]
    assert [e["seq"] for e in events] == list(range(7))
    assert {(e["run_id"], e["incident_id"], e["agent"]) for e in events} == {
        ("run-contract", EXAMPLE_INCIDENT, "investigator")
    }
    assert "run_id" not in events[0]["data"] and "incident_id" not in events[0]["data"]


class FlakyRedis:
    """Fails the first `failures` XADDs the way a dropped connection does."""

    def __init__(self, failures: int):
        self.failures = failures
        self.added: list[dict[str, Any]] = []

    async def xadd(self, stream: str, fields: dict[str, str], **_: Any) -> str:
        if self.failures:
            self.failures -= 1
            raise RedisConnectionError("Connection reset by peer")
        self.added.append(json.loads(fields["event"]))
        return f"{len(self.added)}-0"


async def test_stream_sink_retries_without_skipping_a_seq(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(StreamSink, "RETRY_DELAYS_S", (0, 0, 0))
    redis = FlakyRedis(failures=2)
    sink = StreamSink(redis, "s", run_id="run-1", incident_id=EXAMPLE_INCIDENT)  # type: ignore[arg-type]

    await sink.emit("agent.progress", text="first")
    await sink.emit("agent.progress", text="second")

    assert [(e["seq"], e["data"]["text"]) for e in redis.added] == [(0, "first"), (1, "second")]


async def test_stream_sink_gives_up_when_redis_stays_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(StreamSink, "RETRY_DELAYS_S", (0, 0))
    sink = StreamSink(FlakyRedis(failures=99), "s", run_id="run-1", incident_id=EXAMPLE_INCIDENT)  # type: ignore[arg-type]

    with pytest.raises(RedisConnectionError):
        await sink.emit("agent.progress", text="lost")
    assert sink.seq == 0
