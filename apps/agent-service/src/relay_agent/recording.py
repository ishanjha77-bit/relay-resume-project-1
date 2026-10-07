"""Replayable recordings of investigations.

A recording is a directory:

- ``incident.json``: the incident investigated
- ``llm.jsonl``: every model response, in call order (llm/replay.py)
- ``tools.jsonl``: the tool specs, then every tool result (tools/replay.py)
- ``trace.json``: every event of the run and its final state

``relay-agent investigate --record`` writes one, and so does the service for
each run when ``service.record_dir`` is set. ``relay-agent investigate
--replay`` re-runs one offline, and the golden-trace tests replay those under
``evals/recordings``.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from relay_agent.events import CollectingSink
from relay_agent.graph.investigator import InvestigationState
from relay_agent.graph.schemas import Incident
from relay_agent.llm.replay import RecordingProvider
from relay_agent.llm.types import LLMProvider
from relay_agent.tools.replay import RecordingToolbox
from relay_agent.tools.toolbox import Toolbox

FINAL_KEYS = ("status", "error", "report", "checks", "tool_calls", "llm_calls", "usage", "cost_usd")
RUN_ID = re.compile(r"^run-[0-9a-f]{12}$")


class Recording:
    def __init__(self, run_dir: Path, incident: Incident):
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "incident.json").write_text(incident.model_dump_json(indent=2), encoding="utf-8")
        self.dir = run_dir
        self.events = CollectingSink()

    def provider(self, inner: LLMProvider) -> RecordingProvider:
        return RecordingProvider(inner, self.dir / "llm.jsonl")

    def toolbox(self, inner: Toolbox) -> RecordingToolbox:
        return RecordingToolbox(inner, self.dir / "tools.jsonl")

    def finish(self, state: InvestigationState, elapsed_s: float) -> None:
        trace = {
            "events": self.events.events,
            "final": {k: state.get(k) for k in FINAL_KEYS},
            "elapsed_s": elapsed_s,
        }
        (self.dir / "trace.json").write_text(json.dumps(trace, indent=2, default=str), encoding="utf-8")


def load(run_dir: Path) -> dict[str, Any]:
    """A recording as one JSON document (how the service hands it out)."""

    def lines(name: str) -> list[Any]:
        path = run_dir / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    trace = run_dir / "trace.json"
    return {
        "incident": json.loads((run_dir / "incident.json").read_text(encoding="utf-8")),
        "llm": lines("llm.jsonl"),
        "tools": lines("tools.jsonl"),
        "trace": json.loads(trace.read_text(encoding="utf-8")) if trace.exists() else None,
    }


def save(bundle: dict[str, Any], run_dir: Path) -> None:
    """The inverse of `load`: write a fetched recording back to disk."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "incident.json").write_text(json.dumps(bundle["incident"], indent=2), encoding="utf-8")
    for name, rows in (("llm.jsonl", bundle["llm"]), ("tools.jsonl", bundle["tools"])):
        (run_dir / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    if bundle.get("trace") is not None:
        (run_dir / "trace.json").write_text(json.dumps(bundle["trace"], indent=2), encoding="utf-8")


def prune(record_dir: Path, keep: int) -> None:
    """Keep the newest `keep` recordings."""
    if not record_dir.is_dir():
        return
    runs = sorted(
        (p for p in record_dir.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for old in runs[keep:]:
        shutil.rmtree(old, ignore_errors=True)
