"""Every service run leaves a replayable recording, fetched over HTTP by the eval runner."""

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from relay_agent.config import Budget, ModelRoute, ServiceSettings, Settings
from relay_agent.events import CollectingSink
from relay_agent.graph.investigator import Investigator
from relay_agent.llm.replay import ReplayProvider
from relay_agent.recording import load, save
from relay_agent.service.app import create_app
from relay_agent.service.runner import Runner
from relay_agent.tools.replay import ReplayToolbox
from support import INCIDENT, MODEL, FakeToolbox, answer_turn, report, tool_turn, triage_turn

pytestmark = pytest.mark.anyio

RUN_ID = "run-0123456789ab"


def settings(record_dir: Path) -> Settings:
    return Settings(
        _env_file=None,
        investigator=ModelRoute(model=MODEL),
        service=ServiceSettings(record_dir=str(record_dir), api_token=SecretStr("s3cret")),
    )


async def record_a_run(record_dir: Path, toolbox: FakeToolbox) -> dict[str, Any]:
    @contextlib.asynccontextmanager
    async def tools() -> AsyncIterator[FakeToolbox]:
        yield toolbox

    provider = ReplayProvider(
        [
            triage_turn(),
            tool_turn(("logs__error_summary", {"minutes": 15}), ("metrics__service_health", {})),
            answer_turn(report()),
        ]
    )
    runner = Runner(settings(record_dir), fakeredis.FakeAsyncRedis(decode_responses=True), provider, tools)
    return await runner(INCIDENT, RUN_ID)


async def test_a_service_run_is_recorded_and_replays_offline(tmp_path: Path, toolbox: FakeToolbox) -> None:
    state = await record_a_run(tmp_path, toolbox)
    assert state["status"] == "concluded"

    bundle = load(tmp_path / RUN_ID)
    assert bundle["incident"]["id"] == INCIDENT.id
    # The triage's answer, then the investigator's two; specs and two tool results.
    assert [row["role"] for row in bundle["llm"]] == ["triage", "investigator", "investigator"]
    assert len(bundle["tools"]) == 3
    assert bundle["trace"]["final"]["status"] == "concluded"
    kinds = [e["kind"] for e in bundle["trace"]["events"]]
    assert kinds[:3] == ["llm.completed", "triage.completed", "run.started"]

    copy = tmp_path / "copy"
    save(bundle, copy)  # what the eval runner does with a fetched recording
    replayed = await Investigator(
        ReplayProvider.from_file(copy / "llm.jsonl"),
        ReplayToolbox.from_file(copy / "tools.jsonl"),
        ModelRoute(model=MODEL),
        Budget(),
        CollectingSink(),
    ).run(INCIDENT)
    assert replayed["report"] == state["report"]
    assert replayed["checks"] == state["checks"]


async def test_recordings_are_served_to_the_holder_of_the_token(tmp_path: Path, toolbox: FakeToolbox) -> None:
    await record_a_run(tmp_path, toolbox)

    async def idle(incident: Any, run_id: str) -> dict[str, Any]:
        return {"status": "concluded"}

    app = create_app(settings(tmp_path), redis=fakeredis.FakeAsyncRedis(decode_responses=True), run=idle)
    with TestClient(app) as http:
        auth = {"Authorization": "Bearer s3cret"}
        assert http.get(f"/runs/{RUN_ID}/recording").status_code == 401
        fetched = http.get(f"/runs/{RUN_ID}/recording", headers=auth)
        assert fetched.status_code == 200
        assert fetched.json()["trace"]["final"]["status"] == "concluded"
        assert http.get("/runs/run-ffffffffffff/recording", headers=auth).status_code == 404
        assert http.get("/runs/..%2F..%2Fetc/recording", headers=auth).status_code == 404
