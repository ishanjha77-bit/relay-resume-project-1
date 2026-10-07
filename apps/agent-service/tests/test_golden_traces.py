"""Golden traces: replay recorded live investigations offline and check the verdict.

Each directory in evals/recordings/ is a real run (`make investigate record=...`):
the incident, every Claude response and every tool result. Replaying it drives
the real graph — parsing, routing, budgets, evidence and citation checks — with
no network and no API cost, so any change to the agent's plumbing that breaks a
known-good diagnosis fails CI.
"""

from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest
import yaml
from relay_agent.config import Budget, ModelRoute
from relay_agent.events import CollectingSink
from relay_agent.graph.investigator import Investigator
from relay_agent.graph.schemas import Incident
from relay_agent.llm.replay import ReplayProvider
from relay_agent.tools.replay import ReplayToolbox

ROOT = Path(__file__).resolve().parents[3]
RECORDINGS = sorted(p for p in (ROOT / "evals" / "recordings").glob("*") if (p / "llm.jsonl").exists())


@pytest.mark.parametrize("recording", RECORDINGS, ids=[p.name for p in RECORDINGS])
def test_recorded_investigation_replays_to_the_expected_root_cause(recording: Path) -> None:
    trace = recording / "trace.json"
    if not trace.exists() or json.loads(trace.read_text(encoding="utf-8"))["final"]["status"] != "concluded":
        pytest.skip(f"{recording.name}: the recorded run did not conclude, so it is not a golden trace")
    scenario = yaml.safe_load(
        (ROOT / "evals" / "scenarios" / f"{recording.name}.yaml").read_text(encoding="utf-8")
    )
    expected = scenario["expected"]
    incident = Incident.model_validate_json((recording / "incident.json").read_text(encoding="utf-8"))
    provider = ReplayProvider.from_file(recording / "llm.jsonl")
    toolbox = ReplayToolbox.from_file(recording / "tools.jsonl")
    investigator = Investigator(
        provider,
        toolbox,
        ModelRoute(model="claude-opus-5-5"),
        Budget(tool_calls=15, cost_usd=100),
        CollectingSink(),
    )

    state = anyio.run(investigator.run, incident)

    assert state["status"] == "concluded", state.get("error")
    top = state["report"]["hypotheses"][0]
    assert top["category"] in expected["category"]
    assert top["service"] in expected["service"]
    if expected.get("injection_suspected"):
        assert state["report"]["injection_suspected"]
    assert toolbox.misses == [], "the replay asked for tool results that were never recorded"
    unverified = [c for c in state["checks"] if c["verdict"] != "verified"]
    assert unverified == [], f"citations that don't match their evidence: {unverified}"
