"""Where agent steps go: the terminal, structured logs, and the agent-events Redis stream."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

if TYPE_CHECKING:
    from redis.asyncio import Redis


class EventSink(Protocol):
    async def emit(self, kind: str, /, **data: Any) -> None: ...


class CollectingSink:
    """Keeps every event in memory — for tests and for saving a full trace."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, kind: str, /, **data: Any) -> None:
        # The event kind wins over a field of the same name (approval.requested has an action `kind`).
        self.events.append({**data, "kind": kind, "at": datetime.now(UTC).isoformat()})

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["kind"] == kind]


class FanOut:
    def __init__(self, *sinks: EventSink):
        self._sinks = sinks

    async def emit(self, kind: str, /, **data: Any) -> None:
        for sink in self._sinks:
            await sink.emit(kind, **data)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class StreamSink:
    """Publishes each step to the agent-events stream as a contract envelope
    (contracts/schemas/agent-event.schema.json).

    `seq` counts from 0 within a run and advances only once Redis has the event,
    so a retried XADD whose first reply was lost publishes the same (run_id, seq)
    twice, and platform-api keeps one of them.
    """

    RETRY_DELAYS_S = (0.2, 0.5, 1.0, 2.0, 4.0)

    def __init__(
        self,
        redis: Redis,
        stream: str,
        *,
        run_id: str,
        incident_id: str,
        agent: str = "investigator",
        max_len: int | None = None,
    ):
        self._redis = redis
        self._stream = stream
        self._max_len = max_len
        self.run_id = run_id
        self.incident_id = incident_id
        self.agent = agent
        self.seq = 0

    async def emit(self, kind: str, /, **data: Any) -> None:
        # The envelope carries the run and the incident; data doesn't repeat them.
        data.pop("run_id", None)
        data.pop("incident_id", None)
        payload = json.dumps(
            {
                "event_id": str(uuid.uuid4()),
                "type": kind,
                "run_id": self.run_id,
                "incident_id": self.incident_id,
                "agent": self.agent,
                "seq": self.seq,
                "at": _timestamp(),
                "data": data,
            },
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
        for delay in (*self.RETRY_DELAYS_S, None):
            try:
                await self._redis.xadd(
                    self._stream, {"event": payload}, maxlen=self._max_len, approximate=True
                )
                break
            except (RedisConnectionError, RedisTimeoutError):
                if delay is None:
                    raise
                await asyncio.sleep(delay)
        self.seq += 1


class LogSink:
    """Agent steps as structured log lines, tagged with the run and the incident."""

    def __init__(self, logger: logging.Logger, **context: Any):
        self._log = logger
        self._context = context

    async def emit(self, kind: str, /, **data: Any) -> None:
        extra = {**self._context, "event": kind}
        if kind == "run.started":
            self._log.info(
                "investigating %s with %s (effort %s, %s tools, budget %s calls)",
                data["title"],
                data["model"],
                data["effort"],
                data["tools"],
                data["tool_budget"],
                extra=extra,
            )
        elif kind == "tool.called":
            self._log.info(
                "%s %s %s: %s in %d ms%s",
                data["evidence_id"],
                data["tool"],
                json.dumps(data["arguments"], separators=(",", ":")),
                "error" if data["is_error"] else f"{data['chars']} chars",
                data["latency_ms"],
                f", injection markers {data['injection_markers']}" if data["injection_markers"] else "",
                extra=extra,
            )
        elif kind == "llm.completed":
            self._log.info(
                "%s %s: %d prompt tokens (%d cached), %d output, $%.4f, %d ms",
                data["model"],
                data["stop_reason"],
                data["prompt_tokens"],
                data["cache_read_tokens"],
                data["output_tokens"],
                data["cost_usd"],
                data["latency_ms"],
                extra=extra,
            )
        elif kind == "budget.exhausted":
            self._log.warning("%s", data["reason"], extra=extra)
        elif kind == "run.failed":
            self._log.error("investigation failed: %s", data["error"], extra=extra)
        elif kind == "investigation.concluded":
            hypotheses = data["report"]["hypotheses"]
            top = hypotheses[0] if hypotheses else None
            self._log.info(
                "concluded: %s after %d tool calls, $%.4f",
                f"{top['category']} in {top['service']} (confidence {top['confidence']:.2f})"
                if top
                else "no hypothesis",
                data["tool_calls"],
                data["cost_usd"],
                extra=extra,
            )


_DIM, _BOLD, _CYAN, _YELLOW, _RED, _GREEN, _RESET = (
    "\033[2m",
    "\033[1m",
    "\033[36m",
    "\033[33m",
    "\033[31m",
    "\033[32m",
    "\033[0m",
)


class ConsoleSink:
    """A readable live trace in the terminal."""

    def __init__(self, stream: Any = sys.stdout, color: bool = True):
        self._out = stream
        self._color = color and getattr(stream, "isatty", lambda: False)()

    def _c(self, code: str, text: str) -> str:
        return f"{code}{text}{_RESET}" if self._color else text

    def _print(self, text: str) -> None:
        print(text, file=self._out, flush=True)

    async def emit(self, kind: str, /, **data: Any) -> None:
        if kind == "run.started":
            self._print(self._c(_BOLD, f"▶ investigating {data['incident_id']}: {data['title']}"))
            self._print(
                self._c(
                    _DIM,
                    f"  model {data['model']} · effort {data['effort']} · "
                    f"{data['tools']} tools · budget {data['tool_budget']} calls",
                )
            )
        elif kind == "agent.progress":
            self._print(self._c(_CYAN, f"  ✎ {data['text']}"))
        elif kind == "tool.called":
            args = json.dumps(data["arguments"], separators=(",", ":"))
            flag = self._c(_RED, " ⚠ injection markers") if data.get("injection_markers") else ""
            status = self._c(_RED, "error") if data["is_error"] else f"{data['chars']} chars"
            self._print(
                f"  → {data['evidence_id']} {self._c(_BOLD, data['tool'])} {self._c(_DIM, args)}"
                f" · {status} · {data['latency_ms']} ms{flag}"
            )
        elif kind == "llm.completed":
            self._print(
                self._c(
                    _DIM,
                    f"  · {data['model']} {data['stop_reason']} · in {data['prompt_tokens']} "
                    f"(cached {data['cache_read_tokens']}) out {data['output_tokens']} · "
                    f"${data['cost_usd']:.4f} · {data['latency_ms']} ms",
                )
            )
        elif kind == "budget.exhausted":
            self._print(self._c(_YELLOW, f"  ! {data['reason']}"))
        elif kind == "run.failed":
            self._print(self._c(_RED, f"✖ failed: {data['error']}"))
        elif kind == "investigation.concluded":
            self._print(self._c(_GREEN, "✔ concluded"))
