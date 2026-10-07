"""Record tool results during a live run; serve them back during replay."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from relay_agent.tools.toolbox import Toolbox, ToolOutcome, ToolSpec


def _key(name: str, arguments: dict[str, Any]) -> str:
    return f"{name} {json.dumps(arguments, sort_keys=True)}"


class RecordingToolbox:
    def __init__(self, inner: Toolbox, path: Path):
        self._inner = inner
        self._path = path
        self.specs = inner.specs
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"specs": [s.__dict__ for s in self.specs]}) + "\n", encoding="utf-8")

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        outcome = await self._inner.call(name, arguments)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "key": _key(name, arguments),
                        "text": outcome.text,
                        "is_error": outcome.is_error,
                        "latency_ms": outcome.latency_ms,
                    }
                )
                + "\n"
            )
        return outcome


class ReplayToolbox:
    """Answers a call with the recorded result for the same tool and arguments.

    A call the live run never made returns an explicit "not recorded" error,
    which keeps replays honest when prompts or models change.
    """

    def __init__(self, specs: list[ToolSpec], results: dict[str, list[ToolOutcome]]):
        self.specs = specs
        self._results = results
        self.misses: list[str] = []

    @classmethod
    def from_file(cls, path: Path) -> ReplayToolbox:
        lines = path.read_text(encoding="utf-8").splitlines()
        specs = [ToolSpec(**s) for s in json.loads(lines[0])["specs"]]
        results: dict[str, list[ToolOutcome]] = {}
        for line in lines[1:]:
            if line.strip():
                row = json.loads(line)
                results.setdefault(row["key"], []).append(
                    ToolOutcome(row["text"], row["is_error"], row["latency_ms"])
                )
        return cls(specs, results)

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        key = _key(name, arguments)
        queue = self._results.get(key)
        if not queue:
            self.misses.append(key)
            return ToolOutcome(f"replay: no recorded result for {key}", is_error=True)
        return queue.pop(0) if len(queue) > 1 else queue[0]
