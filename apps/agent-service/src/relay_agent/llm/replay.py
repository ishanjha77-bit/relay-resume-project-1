"""Record a live investigation and replay it deterministically.

A recording is a JSONL file of LLM responses in call order. Replayed with the
recorded tool results (see ``relay_agent.tools.replay``), the graph runs end to
end with no network and no API cost — the basis of the golden-trace tests.
"""

from __future__ import annotations

import json
from pathlib import Path

from relay_agent.llm.types import LLMProvider, LLMRequest, LLMResponse, Usage


def _to_json(response: LLMResponse, request: LLMRequest) -> str:
    return json.dumps(
        {
            "role": request.role,
            "model": response.model,
            "stop_reason": response.stop_reason,
            "content": response.content,
            "usage": response.usage.to_dict(),
            "cost_usd": response.cost_usd,
            "latency_ms": response.latency_ms,
            "refusal_category": response.refusal_category,
        }
    )


class RecordingProvider:
    """Passes calls through to a real provider and appends each response to a file."""

    def __init__(self, inner: LLMProvider, path: Path):
        self.name = f"recording({inner.name})"
        self._inner = inner
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    async def complete(self, request: LLMRequest) -> LLMResponse:
        response = await self._inner.complete(request)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(_to_json(response, request) + "\n")
        return response


class ReplayExhausted(RuntimeError):
    pass


class ReplayProvider:
    """Serves recorded (or scripted) responses in order.

    A response recorded with its agent role answers only a call of that role, so
    the service's recording of a triage and an investigation replays either one;
    scripted responses (no role) answer any call.
    """

    name = "replay"

    def __init__(self, responses: list[LLMResponse], roles: list[str | None] | None = None):
        self._queue = list(zip(roles or [None] * len(responses), responses, strict=True))
        self.requests: list[LLMRequest] = []

    @classmethod
    def from_file(cls, path: Path) -> ReplayProvider:
        responses = []
        roles: list[str | None] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            roles.append(row.get("role"))
            responses.append(
                LLMResponse(
                    content=row["content"],
                    stop_reason=row["stop_reason"],
                    model=row["model"],
                    usage=Usage(**row["usage"]),
                    cost_usd=row["cost_usd"],
                    latency_ms=row["latency_ms"],
                    refusal_category=row.get("refusal_category"),
                )
            )
        return cls(responses, roles)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        for i, (role, response) in enumerate(self._queue):
            if role is None or role == request.role:
                del self._queue[i]
                return response
        raise ReplayExhausted(f"no recorded response left for call #{len(self.requests)} ({request.role})")
