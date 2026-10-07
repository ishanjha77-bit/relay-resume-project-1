"""The incident consumer and the HTTP app, against an in-memory Redis."""

import json
import uuid
from pathlib import Path
from typing import Any

import anyio
import fakeredis
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis.exceptions import ConnectionError as RedisConnectionError
from relay_agent.config import ServiceSettings, Settings, Streams
from relay_agent.graph.schemas import Incident
from relay_agent.service.app import create_app, redis_client
from relay_agent.service.consumer import IncidentConsumer

pytestmark = pytest.mark.anyio

STREAM, GROUP, DEAD = "relay.incidents", "agent-service", "relay.incidents.dead"


def opened(incident_id: str | None = None, number: int = 7) -> str:
    return json.dumps(
        {
            "event_id": str(uuid.uuid4()),
            "type": "incident.opened",
            "at": "2026-10-04T09:00:12.418Z",
            "incident": {
                "id": incident_id or str(uuid.uuid4()),
                "number": number,
                "title": "HighErrorRate on orders",
                "opened_at": "2026-10-04T08:59:40Z",
                "namespace": "sandbox",
                "alerts": [
                    {
                        "name": "HighErrorRate",
                        "service": "orders",
                        "severity": "critical",
                        "state": "firing",
                        "since": "2026-10-04T08:59:40Z",
                        "summary": "orders: 64% of requests are failing",
                    }
                ],
            },
        }
    )


class FakeRun:
    """Stands in for the Runner: records what it was asked to investigate."""

    def __init__(self, errors: list[Exception] | None = None):
        self.calls: list[tuple[Incident, str]] = []
        self.errors = list(errors or [])
        self.gate: anyio.Event | None = None

    async def __call__(self, incident: Incident, run_id: str) -> dict[str, Any]:
        self.calls.append((incident, run_id))
        if self.gate:
            await self.gate.wait()
        if self.errors:
            raise self.errors.pop(0)
        return {"status": "concluded"}


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis(decode_responses=True)


def make_consumer(redis: Any, run: FakeRun, name: str = "worker-1", **service: Any) -> IncidentConsumer:
    return IncidentConsumer(
        redis, Streams(), ServiceSettings(**service), run, name, block_ms=10, reclaim_idle_ms=0
    )


async def started(redis: Any, run: FakeRun, **service: Any) -> IncidentConsumer:
    consumer = make_consumer(redis, run, **service)
    await consumer.ensure_group()
    return consumer


async def pending(redis: Any) -> int:
    return (await redis.xpending(STREAM, GROUP))["pending"]


async def test_each_incident_is_investigated_once_and_acknowledged(redis: Any) -> None:
    run = FakeRun()
    consumer = await started(redis, run)
    incident_id = str(uuid.uuid4())
    await redis.xadd(STREAM, {"event": opened(incident_id, number=7)})

    assert await consumer.read() == 1
    await consumer.wait_idle()

    [(incident, run_id)] = run.calls
    assert (incident.id, incident.number, incident.alerts[0].service) == (incident_id, 7, "orders")
    assert await pending(redis) == 0
    assert await redis.get(f"relay:agent:incident:{incident_id}") == f"done:{run_id}"

    # The outbox re-publishes after a crash: the copy is acknowledged, not re-investigated.
    await redis.xadd(STREAM, {"event": opened(incident_id)})
    await consumer.read()
    await consumer.wait_idle()
    assert len(run.calls) == 1
    assert await pending(redis) == 0


async def test_a_copy_read_while_the_first_still_runs_is_skipped(redis: Any) -> None:
    run = FakeRun()
    run.gate = anyio.Event()
    consumer = await started(redis, run)
    incident_id = str(uuid.uuid4())
    await redis.xadd(STREAM, {"event": opened(incident_id)})
    await redis.xadd(STREAM, {"event": opened(incident_id)})

    assert await consumer.read() == 2
    await anyio.sleep(0.05)
    assert await pending(redis) == 1  # the duplicate is already acknowledged
    run.gate.set()
    await consumer.wait_idle()

    assert len(run.calls) == 1
    assert await pending(redis) == 0


async def test_malformed_messages_are_dead_lettered(redis: Any) -> None:
    run = FakeRun()
    consumer = await started(redis, run)
    await redis.xadd(STREAM, {"event": "{not json"})
    await redis.xadd(STREAM, {"event": json.dumps({"type": "incident.closed"})})
    await redis.xadd(STREAM, {"payload": "x"})

    while await consumer.read():  # two run slots: three messages take two reads
        await consumer.wait_idle()

    assert run.calls == []
    assert await pending(redis) == 0
    reasons = [fields["reason"] for _, fields in await redis.xrange(DEAD)]
    assert reasons[0].startswith("malformed incident.opened: message: Invalid JSON")
    assert "type: Input should be 'incident.opened'" in reasons[1]
    assert reasons[2] == "no `event` field"


async def test_a_run_that_could_not_report_is_retried_by_the_reclaimer(redis: Any) -> None:
    run = FakeRun(errors=[RedisConnectionError("Connection reset by peer")])
    consumer = await started(redis, run)
    incident_id = str(uuid.uuid4())
    await redis.xadd(STREAM, {"event": opened(incident_id)})

    await consumer.read()
    await consumer.wait_idle()
    assert await pending(redis) == 1  # left for a retry
    await redis.delete(f"relay:agent:incident:{incident_id}")  # the lease lapses

    assert await consumer.reclaim() == 1
    await consumer.wait_idle()
    assert len(run.calls) == 2
    assert await pending(redis) == 0


async def test_a_message_that_keeps_failing_is_dead_lettered(redis: Any) -> None:
    run = FakeRun(errors=[RedisConnectionError("down")] * 5)
    consumer = await started(redis, run, max_deliveries=2)
    incident_id = str(uuid.uuid4())
    await redis.xadd(STREAM, {"event": opened(incident_id)})

    await consumer.read()  # delivery 1
    await consumer.wait_idle()
    await redis.delete(f"relay:agent:incident:{incident_id}")
    await consumer.reclaim()  # delivery 2
    await consumer.wait_idle()
    await consumer.reclaim()  # out of attempts

    assert len(run.calls) == 2
    assert await pending(redis) == 0
    [(_, dead)] = await redis.xrange(DEAD)
    assert dead["reason"] == "gave up after 2 deliveries"
    assert json.loads(dead["event"])["incident"]["id"] == incident_id


async def test_a_restarted_consumer_resumes_what_it_had_read(redis: Any) -> None:
    run = FakeRun()
    await started(redis, run)
    await redis.xadd(STREAM, {"event": opened()})
    await redis.xreadgroup(GROUP, "worker-1", {STREAM: ">"}, count=1)  # read, then crashed

    consumer = make_consumer(redis, run, name="worker-1")
    assert await consumer.read() == 0  # nothing new
    assert await consumer.read(pending=True) == 1
    await consumer.wait_idle()
    assert len(run.calls) == 1
    assert await pending(redis) == 0


async def test_never_reads_more_than_it_can_run(redis: Any) -> None:
    run = FakeRun()
    run.gate = anyio.Event()
    consumer = await started(redis, run, max_concurrent_runs=1)
    await redis.xadd(STREAM, {"event": opened()})
    await redis.xadd(STREAM, {"event": opened()})

    assert await consumer.read() == 1
    assert await consumer.read() == 0
    run.gate.set()
    await consumer.wait_idle()
    assert await consumer.read() == 1
    await consumer.wait_idle()
    assert len(run.calls) == 2


# ---------------------------------------------------------------------------- app


def test_the_redis_client_outwaits_the_blocking_read() -> None:
    # The in-memory Redis ignores BLOCK; a real one replies only when it expires.
    client = redis_client("redis://localhost:6379/0")
    assert client.connection_pool.connection_kwargs["socket_timeout"] * 1000 > IncidentConsumer.BLOCK_MS


EXAMPLES = Path(__file__).resolve().parents[3] / "contracts" / "examples"


def client(run: FakeRun, token: str | None = "s3cret", resume: Any = None) -> TestClient:
    settings = Settings(
        _env_file=None,
        service=ServiceSettings(api_token=SecretStr(token) if token else None),
    )
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    return TestClient(create_app(settings, redis=redis, run=run, resume=resume))


def incident_body() -> dict[str, Any]:
    return json.loads(opened(number=9))["incident"]


def test_probes_and_metrics() -> None:
    with client(FakeRun()) as http:
        assert http.get("/healthz").json() == {"status": "ok"}
        assert http.get("/readyz").json() == {"status": "ok"}
        assert "relay_agent_runs_in_flight" in http.get("/metrics").text


def test_runs_on_demand_need_the_token() -> None:
    run = FakeRun()
    with client(run) as http:
        body = incident_body()
        assert http.post("/runs", json=body).status_code == 401
        assert http.post("/runs", json=body, headers={"Authorization": "Bearer nope"}).status_code == 401

        accepted = http.post("/runs", json=body, headers={"Authorization": "Bearer s3cret"})
        assert accepted.status_code == 202
        assert accepted.json()["incident_id"] == body["id"]
        assert accepted.json()["run_id"].startswith("run-")
    assert [incident.id for incident, _ in run.calls] == [body["id"]]


def test_a_decision_can_resume_its_run_by_hand() -> None:
    decision = json.loads((EXAMPLES / "approval-decision.json").read_text(encoding="utf-8"))
    applied: list[Any] = []

    async def resume(d: Any) -> None:
        applied.append(d)

    auth = {"Authorization": "Bearer s3cret"}
    with client(FakeRun(), resume=resume) as http:
        path = f"/runs/{decision['run_id']}/resume"
        assert http.post(path, json=decision).status_code == 401
        assert http.post("/runs/run-other/resume", json=decision, headers=auth).status_code == 422
        done = http.post(path, json=decision, headers=auth)
        assert done.status_code == 200 and done.json()["decision"] == decision["decision"]
    assert [d.approval_id for d in applied] == [decision["approval_id"]]

    with client(FakeRun()) as http:  # tests run without a runner: nothing to resume
        assert http.post(path, json=decision, headers=auth).status_code == 409


def test_runs_on_demand_are_off_without_a_token() -> None:
    with client(FakeRun(), token=None) as http:
        assert http.post("/runs", json=incident_body()).status_code == 403
