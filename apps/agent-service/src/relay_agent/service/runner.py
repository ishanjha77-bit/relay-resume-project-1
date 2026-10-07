"""One investigation as the service runs it: Claude, the MCP tool servers, and
every step published to the agent-events stream."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from relay_agent.config import Settings
from relay_agent.events import EventSink, FanOut, LogSink, StreamSink
from relay_agent.graph.fixer import Fixer
from relay_agent.graph.investigator import InvestigationState, Investigator
from relay_agent.graph.reviewer import Reviewer, apply
from relay_agent.graph.schemas import Incident
from relay_agent.graph.triage import Triage, Triager
from relay_agent.llm.types import LLMProvider, LLMResponse
from relay_agent.recording import Recording, prune
from relay_agent.service.metrics import RUN_COST, RUN_SECONDS, RUNS, MetricsSink
from relay_agent.tools.mcp_toolbox import McpToolbox
from relay_agent.tools.toolbox import Toolbox
from relay_agent.tracing import TracedProvider, TracedToolbox, tracer

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

    from langgraph.checkpoint.base import BaseCheckpointSaver
    from redis.asyncio import Redis

    from relay_agent.service.approvals import ApprovalDecision

log = logging.getLogger("relay_agent.run")

# A slow triage must not hold the investigation up for long.
TRIAGE_TIMEOUT_S = 90


class Runner:
    """Runs an investigation and reports it on the agent-events stream, whatever happens.

    A run that fails (no API key, tool servers unreachable, a timeout, a bug)
    still ends with a `run.failed` event, so the incident never waits on a dead
    run. The one exception is Redis itself failing: then platform-api can't be
    told anything, and the error propagates so the incident is retried later.
    """

    def __init__(
        self,
        settings: Settings,
        redis: Redis,
        provider: LLMProvider | None,
        toolbox: Callable[[], AbstractAsyncContextManager[Toolbox]] | None = None,
        *,
        unavailable: str | None = None,
        checkpoints: Callable[[], Awaitable[BaseCheckpointSaver | None]] | None = None,
    ):
        self._settings = settings
        self._redis = redis
        self._provider = provider
        # Why there is no provider (a missing API key), reported by every run.
        self._unavailable = unavailable or "no LLM provider is configured"
        self._toolbox = toolbox or (lambda: McpToolbox(settings.mcp_servers))
        # Where the fixer keeps runs that wait for an approval (Redis in the service).
        self._checkpoints = checkpoints

    async def __call__(self, incident: Incident, run_id: str) -> InvestigationState:
        # One trace per run: the investigation, the review and the fix proposal under it.
        with tracer.start_as_current_span(
            "investigation",
            attributes={
                "relay.incident.id": incident.id,
                "relay.incident.title": incident.title,
                "relay.run.id": run_id,
            },
        ) as span:
            state = await self._run(incident, run_id)
            span.set_attribute("relay.run.status", str(state.get("status")))
            span.set_attribute("relay.run.tool_calls", int(state.get("tool_calls", 0)))
            return state

    async def _run(self, incident: Incident, run_id: str) -> InvestigationState:
        context = {"run_id": run_id, "incident_id": incident.id, "incident_number": incident.number}
        stream = self._stream(run_id, incident.id)
        sink: EventSink = FanOut(stream, LogSink(log, **context), MetricsSink())
        recording = self._recording(incident, run_id)
        if recording:
            sink = FanOut(sink, recording.events)
        timeout_s = self._settings.service.run_timeout_s
        deadline = asyncio.timeout(timeout_s)
        started = time.monotonic()
        try:
            if self._provider is None:
                raise RuntimeError(self._unavailable)
            async with deadline, self._toolbox() as toolbox:
                provider = TracedProvider(recording.provider(self._provider) if recording else self._provider)
                tools = TracedToolbox(recording.toolbox(toolbox) if recording else toolbox)
                triage = await self._triage(incident, provider, tools, stream, context, recording)
                investigator = Investigator(
                    provider, tools, self._settings.investigator, self._settings.budget, sink
                )
                state = await investigator.run(incident, run_id=run_id, notes=triage and triage.notes())
                if state.get("status") == "concluded":
                    report = await self._review(incident, run_id, state, stream, context)
                    await self._propose_fix(
                        incident, run_id, {**state, "report": report}, toolbox, stream, context
                    )
                    await sink.emit("run.finished", status="concluded")
        except (RedisConnectionError, RedisTimeoutError):
            raise
        except Exception as e:
            if isinstance(e, TimeoutError) and deadline.expired():
                error = f"investigation timed out after {timeout_s} s"
            else:
                log.exception("investigation crashed", extra=context)
                error = f"{type(e).__name__}: {e}"
            state = await _failed(sink, error)
        elapsed = time.monotonic() - started
        if recording:
            recording.finish(state, elapsed)
        RUNS.labels(state.get("status", "unknown")).inc()
        RUN_SECONDS.observe(elapsed)
        RUN_COST.observe(state.get("cost_usd", 0.0))
        return state

    async def _triage(
        self,
        incident: Incident,
        provider: LLMProvider,
        tools: Toolbox,
        stream: StreamSink,
        context: dict[str, Any],
        recording: Recording | None,
    ) -> Triage | None:
        """A small model's first look: how bad, where to start, and the runbooks and past
        incidents the alerts resemble. Never fails the run: without a triage, the
        investigator starts from the alerts alone."""
        if not self._settings.triage_enabled:
            return None
        sink = self._stream(stream.run_id, incident.id, agent="triage")
        sink.seq = stream.seq
        events: EventSink = FanOut(sink, LogSink(log, **context), MetricsSink())
        if recording:
            events = FanOut(events, recording.events)
        with tracer.start_as_current_span(
            "invoke_agent triage",
            attributes={"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "triage"},
        ):
            try:
                async with asyncio.timeout(TRIAGE_TIMEOUT_S):
                    triage = await Triager(provider, tools, self._settings.triage).triage(incident)
            except Exception as e:
                log.warning(
                    "triage failed; investigating from the alerts: %s: %s", type(e).__name__, e, extra=context
                )
                return None
        if triage.response:
            await events.emit("llm.completed", **llm_event(triage.response))
        await events.emit("triage.completed", **triage.event())
        stream.seq = sink.seq
        return triage

    async def _review(
        self,
        incident: Incident,
        run_id: str,
        state: InvestigationState,
        stream: StreamSink,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """The reviewer checks the verdict before anything acts on it. Returns the
        reviewed report: confidence lowered, hypotheses re-ranked. Never fails the
        run: without a review, the investigator's verdict stands."""
        report = dict(state.get("report") or {})
        if not self._settings.reviewer_enabled or not report.get("hypotheses") or self._provider is None:
            return report
        sink = self._stream(run_id, incident.id, agent="reviewer")
        sink.seq = stream.seq
        events = FanOut(sink, LogSink(log, **context), MetricsSink())
        evidence = {e["id"]: e for e in state.get("evidence", [])}
        try:
            review, response = await Reviewer(TracedProvider(self._provider), self._settings.reviewer).review(
                incident, report, evidence, list(state.get("checks", []))
            )
        except (RedisConnectionError, RedisTimeoutError):
            raise
        except Exception as e:
            log.warning("the reviewer failed, the verdict stands: %s: %s", type(e).__name__, e, extra=context)
            return report
        await events.emit("llm.completed", **llm_event(response))
        if review is None:
            log.warning("the reviewer gave no usable review; the verdict stands", extra=context)
            stream.seq = sink.seq
            return report
        outcomes = apply(report, review)
        await events.emit(
            "review.completed",
            model=response.model,
            order=[o.original_rank for o in outcomes],
            reviews=[
                {
                    "rank": o.original_rank,
                    "verdict": o.verdict,
                    "confidence": o.hypothesis["confidence"],
                    "original_confidence": o.original_confidence,
                    "reason": o.reason,
                }
                for o in outcomes
            ],
        )
        stream.seq = sink.seq
        return {**report, "hypotheses": [o.hypothesis for o in outcomes]}

    async def _propose_fix(
        self,
        incident: Incident,
        run_id: str,
        state: InvestigationState,
        toolbox: Toolbox,
        stream: StreamSink,
        context: dict[str, Any],
    ) -> None:
        """After a verdict, the fixer may ask a human to approve undoing the change
        behind it. Never fails the investigation: the verdict stands either way."""
        if not self._settings.fixer.enabled or self._checkpoints is None:
            return
        try:
            checkpointer = await self._checkpoints()
            if checkpointer is None:
                return
            fixer = Fixer(
                toolbox, self._stream(run_id, incident.id, agent="fixer"), also=(LogSink(log, **context),)
            )
            fixed = await fixer.start(
                checkpointer, incident, run_id, dict(state.get("report") or {}), seq=stream.seq
            )
            stream.seq = fixed.get("seq", stream.seq)
        except (RedisConnectionError, RedisTimeoutError):
            raise
        except Exception:
            log.exception("the fixer failed; the verdict stands", extra=context)

    async def resume(self, decision: ApprovalDecision) -> None:
        """A human decided on a fix this service proposed: resume the run that waits for it."""
        context = {"run_id": decision.run_id, "incident_id": decision.incident_id}
        checkpointer = await self._checkpoints() if self._checkpoints else None
        if checkpointer is None:
            log.error("no checkpoint store: cannot resume %s", decision.run_id, extra=context)
            return
        stream = self._stream(decision.run_id, decision.incident_id, agent="fixer")
        # The run went on publishing after the fixer paused (run.finished, at least), so the
        # paused graph's next seq is taken. A time-based one sorts after all of it.
        stream.seq = int(time.time())
        async with asyncio.timeout(self._settings.service.run_timeout_s), self._toolbox() as toolbox:
            fixer = Fixer(toolbox, stream, also=(LogSink(log, **context),))
            outcome = await fixer.resume(checkpointer, decision.run_id, decision.model_dump())
            if outcome is not None:
                return
            if await fixer.known(checkpointer, decision.run_id):
                log.info("approval %s was already applied", decision.approval_id, extra=context)
                return
        if decision.decision == "approved":
            # The run's state expired before the decision came. Nothing is executed
            # without it, and the incident must say so.
            await stream.emit(
                "action.failed",
                approval_id=decision.approval_id,
                error="the run stopped waiting for this approval before it arrived; investigate again to get a new proposal",
            )

    def _stream(self, run_id: str, incident_id: str, *, agent: str = "investigator") -> StreamSink:
        return StreamSink(
            self._redis,
            self._settings.streams.agent_events,
            run_id=run_id,
            incident_id=incident_id,
            agent=agent,
            max_len=self._settings.streams.agent_events_max_len,
        )

    def _recording(self, incident: Incident, run_id: str) -> Recording | None:
        root = self._settings.service.record_dir
        if not root:
            return None
        try:
            prune(Path(root), self._settings.service.keep_recordings - 1)
            return Recording(Path(root) / run_id, incident)
        except OSError as e:  # a full or read-only disk must not stop the investigation
            log.warning("not recording %s: %s", run_id, e)
            return None


def llm_event(response: LLMResponse) -> dict[str, Any]:
    """The llm.completed event of one model call (contracts/schemas/agent-event.schema.json)."""
    return {
        "model": response.model,
        "stop_reason": response.stop_reason,
        "prompt_tokens": response.usage.prompt_tokens,
        "cache_read_tokens": response.usage.cache_read_tokens,
        "output_tokens": response.usage.output_tokens,
        "cost_usd": response.cost_usd,
        "latency_ms": response.latency_ms,
        "request_id": response.request_id,
    }


async def _failed(sink: EventSink, error: str) -> InvestigationState:
    await sink.emit("run.failed", error=error)
    return {"status": "failed", "error": error}
