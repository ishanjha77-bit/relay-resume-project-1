"""The investigator: a ReAct loop expressed as a LangGraph state machine.

    START → think ──(tool_use)──→ act ──→ think
              │ └─(no answer yet)─→ think   (nudged, at most twice)
              └─(report)──→ conclude → END

Each LLM call and each batch of tool calls is its own graph step, so a run is
checkpointed between steps and can resume after a crash (and, in later phases,
pause for human approval). The conversation is append-only: assistant turns are
stored exactly as returned, which keeps Claude's thinking blocks valid and the
prompt cache warm.
"""

from __future__ import annotations

import asyncio
import operator
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from relay_agent.config import Budget, ModelRoute
from relay_agent.events import EventSink
from relay_agent.graph.evidence import Evidence, make_evidence, verify_citations, wrap
from relay_agent.graph.prompts import (
    ANSWER_REMINDER,
    BUDGET_EXHAUSTED,
    COST_EXHAUSTED,
    INVESTIGATOR_SYSTEM,
    incident_brief,
)
from relay_agent.graph.schemas import Incident, InvestigationReport, report_schema
from relay_agent.llm.types import LLMProvider, LLMRequest, LLMResponse, Usage
from relay_agent.tools.toolbox import Toolbox, allowed_for

MAX_NUDGES = 2


class InvestigationState(TypedDict, total=False):
    incident: dict[str, Any]
    messages: Annotated[list[dict[str, Any]], operator.add]
    evidence: Annotated[list[dict[str, Any]], operator.add]
    tool_calls: int
    llm_calls: int
    nudges: int
    usage: dict[str, int]
    cost_usd: float
    report: dict[str, Any] | None
    checks: list[dict[str, Any]]
    status: str
    error: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class Investigator:
    provider: LLMProvider
    toolbox: Toolbox
    route: ModelRoute
    budget: Budget
    events: EventSink

    def __post_init__(self) -> None:
        self.specs = allowed_for("investigator", self.toolbox.specs)
        # Fixed for the whole run: part of the cached, thinking-bound prompt prefix.
        self.tools = [spec.to_anthropic() for spec in self.specs]
        self.schema = report_schema()
        self.max_llm_calls = self.budget.tool_calls + 2 * MAX_NUDGES + 2

    # ----------------------------------------------------------------- graph
    def graph(self, checkpointer: BaseCheckpointSaver | None = None):
        g = StateGraph(InvestigationState)
        g.add_node("think", self.think)
        g.add_node("act", self.act)
        g.add_node("conclude", self.conclude)
        g.add_edge(START, "think")
        g.add_conditional_edges("think", self.after_think, ["act", "think", "conclude", END])
        g.add_edge("act", "think")
        g.add_edge("conclude", END)
        return g.compile(checkpointer=checkpointer)

    async def run(
        self,
        incident: Incident,
        run_id: str | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
        notes: str | None = None,
    ) -> InvestigationState:
        """Investigate `incident`; `notes` (the triage's) join the alerts in the brief."""
        run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        await self.events.emit(
            "run.started",
            run_id=run_id,
            incident_id=incident.id,
            title=incident.title,
            model=self.route.model,
            effort=self.route.effort,
            tools=len(self.tools),
            tool_budget=self.budget.tool_calls,
        )
        initial: InvestigationState = {
            "incident": incident.model_dump(),
            "messages": [
                {"role": "user", "content": incident_brief(incident, self.budget.tool_calls, _now(), notes)}
            ],
            "evidence": [],
            "tool_calls": 0,
            "llm_calls": 0,
            "nudges": 0,
            "usage": Usage().to_dict(),
            "cost_usd": 0.0,
            "report": None,
            "status": "investigating",
        }
        graph = self.graph(checkpointer or InMemorySaver())
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": 4 * self.max_llm_calls}
        return await graph.ainvoke(initial, config)

    # ----------------------------------------------------------------- nodes
    async def think(self, state: InvestigationState) -> dict[str, Any]:
        if state["llm_calls"] >= self.max_llm_calls:
            return await self._fail("LLM call limit reached without a report")
        answer_now = (
            state["tool_calls"] >= self.budget.tool_calls
            or state["cost_usd"] >= self.budget.cost_usd
            or state["nudges"] > 0
        )
        request = LLMRequest(
            role="investigator",
            model=self.route.model,
            system=INVESTIGATOR_SYSTEM,
            messages=state["messages"],
            tools=self.tools,
            output_schema=self.schema,
            effort=self.route.effort,
            max_tokens=self.route.max_tokens,
            fallbacks=self.route.fallbacks,
            answer_now=answer_now,
        )
        try:
            response = await self.provider.complete(request)
        except Exception as e:
            return await self._fail(f"{type(e).__name__}: {e}")

        await self._record_llm(response)
        update: dict[str, Any] = {
            "messages": [{"role": "assistant", "content": response.content}],
            "llm_calls": state["llm_calls"] + 1,
            "usage": (Usage(**state["usage"]) + response.usage).to_dict(),
            "cost_usd": state["cost_usd"] + response.cost_usd,
        }
        if response.stop_reason == "refusal":
            update.update(status="refused", error=f"model declined (category: {response.refusal_category})")
            await self.events.emit("run.failed", error=update["error"])
            return update
        if response.tool_uses():
            return update
        try:
            report = InvestigationReport.model_validate_json(response.text())
        except ValidationError as e:
            if state["nudges"] >= MAX_NUDGES:
                return {**update, **await self._fail(f"no valid report after {MAX_NUDGES} reminders: {e}")}
            update["messages"].append({"role": "user", "content": ANSWER_REMINDER})
            update["nudges"] = state["nudges"] + 1
            return update
        update["report"] = report.model_dump(mode="json")
        return update

    def after_think(self, state: InvestigationState) -> str:
        if state.get("status") in ("failed", "refused"):
            return END
        if state.get("report"):
            return "conclude"
        last = state["messages"][-1]
        if last["role"] == "assistant" and any(b.get("type") == "tool_use" for b in last["content"]):
            return "act"
        return "think"

    async def act(self, state: InvestigationState) -> dict[str, Any]:
        calls = [b for b in state["messages"][-1]["content"] if b.get("type") == "tool_use"]
        allowed = {spec.name for spec in self.specs}
        used = state["tool_calls"]
        planned: list[dict[str, Any]] = []
        rejected: dict[str, str] = {}
        for call in calls:
            if call["name"] not in allowed:
                rejected[call["id"]] = f"Tool {call['name']!r} is not available to the investigator."
            elif used >= self.budget.tool_calls:
                rejected[call["id"]] = "Tool budget exhausted; this call was not executed."
            else:
                used += 1
                planned.append(call)

        outcomes = await asyncio.gather(
            *(self.toolbox.call(c["name"], c.get("input") or {}) for c in planned)
        )
        next_index = len(state["evidence"]) + 1
        evidence: dict[str, Evidence] = {}
        for offset, (call, outcome) in enumerate(zip(planned, outcomes, strict=True)):
            item = make_evidence(
                next_index + offset,
                call["name"],
                call.get("input") or {},
                outcome.text,
                outcome.is_error,
                outcome.latency_ms,
            )
            evidence[call["id"]] = item
            await self.events.emit(
                "tool.called",
                evidence_id=item.id,
                tool=item.tool,
                arguments=item.arguments,
                is_error=item.is_error,
                latency_ms=item.latency_ms,
                chars=len(item.text),
                injection_markers=item.injection_markers,
                output=item.text,
            )

        content: list[dict[str, Any]] = []
        for call in calls:  # one result per tool_use, in the same order
            if call["id"] in evidence:
                item = evidence[call["id"]]
                content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call["id"],
                        "content": wrap(item),
                        "is_error": item.is_error,
                    }
                )
            else:
                content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call["id"],
                        "content": rejected[call["id"]],
                        "is_error": True,
                    }
                )

        spent = state["cost_usd"]
        if used >= self.budget.tool_calls:
            note = BUDGET_EXHAUSTED.format(used=used, limit=self.budget.tool_calls)
        elif spent >= self.budget.cost_usd:
            note = COST_EXHAUSTED.format(spent=spent, limit=self.budget.cost_usd)
        else:
            note = None
        if note:
            content.append({"type": "text", "text": note})
            await self.events.emit("budget.exhausted", reason=note)

        return {
            "messages": [{"role": "user", "content": content}],
            "evidence": [e.to_dict() for e in evidence.values()],
            "tool_calls": used,
        }

    async def conclude(self, state: InvestigationState) -> dict[str, Any]:
        report = InvestigationReport.model_validate(state["report"])
        ledger = {e["id"]: Evidence(**e) for e in state["evidence"]}
        checks = [c.__dict__ for c in verify_citations(report, ledger)]
        await self.events.emit(
            "investigation.concluded",
            report=state["report"],
            checks=checks,
            tool_calls=state["tool_calls"],
            llm_calls=state["llm_calls"],
            cost_usd=state["cost_usd"],
            usage=state["usage"],
        )
        return {"status": "concluded", "checks": checks}

    # --------------------------------------------------------------- helpers
    async def _record_llm(self, response: LLMResponse) -> None:
        for note in response.progress_notes():
            await self.events.emit("agent.progress", text=note)
        await self.events.emit(
            "llm.completed",
            model=response.model,
            stop_reason=response.stop_reason,
            prompt_tokens=response.usage.prompt_tokens,
            cache_read_tokens=response.usage.cache_read_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=response.cost_usd,
            latency_ms=response.latency_ms,
            request_id=response.request_id,
        )

    async def _fail(self, error: str) -> dict[str, Any]:
        await self.events.emit("run.failed", error=error)
        return {"status": "failed", "error": error}
