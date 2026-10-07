"""Test support: a scripted LLM, an in-memory toolbox and canned reports."""

from __future__ import annotations

import json
from typing import Any

from relay_agent.config import Budget, ModelRoute
from relay_agent.events import CollectingSink
from relay_agent.graph.investigator import Investigator
from relay_agent.graph.schemas import Alert, Incident
from relay_agent.llm.replay import ReplayProvider
from relay_agent.llm.types import LLMResponse, Usage
from relay_agent.tools.toolbox import ToolOutcome, ToolSpec

MODEL = "claude-opus-5-5"


def spec(name: str, read_only: bool = True) -> ToolSpec:
    server, remote = name.split("__", 1)
    return ToolSpec(name, server, remote, f"{remote} tool", {"type": "object", "properties": {}}, read_only)


class FakeToolbox:
    def __init__(self, outputs: dict[str, str], specs: list[ToolSpec] | None = None):
        self.outputs = outputs
        self.specs = specs or [spec(name) for name in sorted(outputs)]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, arguments))
        return ToolOutcome(self.outputs[name], latency_ms=5)


def tool_turn(
    *calls: tuple[str, dict[str, Any]], note: str = "Checking logs and metrics first."
) -> LLMResponse:
    content: list[dict[str, Any]] = [{"type": "thinking", "thinking": note, "signature": "sig-1"}]
    content += [
        {"type": "tool_use", "id": f"toolu_{i}", "name": name, "input": args}
        for i, (name, args) in enumerate(calls)
    ]
    return LLMResponse(
        content,
        "tool_use",
        MODEL,
        Usage(input_tokens=1200, output_tokens=150, cache_write_5m_tokens=4000),
        0.0,
        900,
    )


def answer_turn(report: dict[str, Any]) -> LLMResponse:
    content = [
        {"type": "thinking", "thinking": "", "signature": "sig-2"},
        {"type": "text", "text": json.dumps(report)},
    ]
    return LLMResponse(
        content,
        "end_turn",
        MODEL,
        Usage(input_tokens=300, output_tokens=600, cache_read_tokens=5200),
        0.0,
        2100,
    )


def report(quote: str = "slow response from psp", evidence_id: str = "E1") -> dict[str, Any]:
    return {
        "summary": "The payment provider slowed down; orders timed out waiting on payments.",
        "impact": "About 60% of checkouts failed.",
        "affected_services": ["orders", "gateway", "payments"],
        "started_at": "2026-10-04T06:09:40Z",
        "hypotheses": [
            {
                "category": "dependency_latency",
                "service": "psp",
                "component": "PSP client POST /v1/charges",
                "summary": "PSP responses take 2.5-3.3 s, beyond orders' 2 s timeout on payments.",
                "confidence": 0.86,
                "evidence": [{"evidence_id": evidence_id, "quote": quote, "shows": "PSP latency"}],
                "suggested_fix": "Fail over to the secondary PSP or raise the timeout with a circuit breaker.",
            }
        ],
        "injection_suspected": False,
        "injection_evidence_ids": [],
    }


INCIDENT = Incident(
    id="INC-test",
    title="HighErrorRate on orders",
    opened_at="2026-10-04T06:11:00Z",
    alerts=[
        Alert(
            name="HighErrorRate",
            service="orders",
            severity="critical",
            since="2026-10-04T06:11:00Z",
            summary="orders: 62% of requests are failing",
        )
    ],
)

TRIAGE = {
    "severity": "critical",
    "service": "orders",
    "summary": "Most checkouts fail: orders returns errors to the gateway.",
    "leads": ["Check orders' calls to payments and the PSP (runbook:dependency-latency)."],
}


def triage_turn(assessment: dict[str, Any] | None = None) -> LLMResponse:
    """The triage model's answer, which a service run asks for before investigating."""
    return answer_turn(assessment or TRIAGE)


LOGS = '{"patterns":[{"service":"payments","level":"WARN","count":212,"pattern":"slow response from psp"}]}'
METRICS = '{"services":{"payments":{"p95_s":{"now":4.8,"before":0.08}}}}'


def make_investigator(
    responses: list[LLMResponse],
    toolbox: FakeToolbox,
    tool_budget: int = 15,
) -> tuple[Investigator, ReplayProvider, CollectingSink]:
    provider = ReplayProvider(responses)
    sink = CollectingSink()
    investigator = Investigator(
        provider,
        toolbox,
        ModelRoute(model=MODEL, effort="medium"),
        Budget(tool_calls=tool_budget, cost_usd=5.0),
        sink,
    )
    return investigator, provider, sink
