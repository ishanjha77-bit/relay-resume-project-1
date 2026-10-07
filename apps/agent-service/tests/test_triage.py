"""Triage: the knowledge-base searches, the model's assessment, and its place in a run."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from relay_agent.config import ModelRoute, Settings
from relay_agent.graph.triage import PASSAGE_CHARS, ServiceGraph, Triager, query_for, search
from relay_agent.llm.replay import ReplayExhausted, ReplayProvider
from relay_agent.llm.types import LLMRequest
from relay_agent.service.runner import Runner
from relay_agent.tools.toolbox import ToolOutcome
from support import INCIDENT as SUPPORT_INCIDENT
from support import LOGS, METRICS, TRIAGE, FakeToolbox, answer_turn, report, tool_turn, triage_turn
from test_contracts import errors

pytestmark = pytest.mark.anyio

ROUTE = ModelRoute(model="gemma-4-26b-a4b-it")
# Published events carry the incident id, a UUID in the contract.
INCIDENT = SUPPORT_INCIDENT.model_copy(update={"id": "5d0c7a0e-0000-4000-8000-000000000003"})
EXAMPLES = Path(__file__).resolve().parents[3] / "contracts" / "examples"


def found(kind: str, *docs: str) -> str:
    """A runbooks__search answer: two passages of each document, best first."""
    rows = [
        {
            "doc_id": f"{kind}:{doc}",
            "kind": kind,
            "title": doc.replace("-", " "),
            "section": section,
            "matched_by": "both",
            "score": round(0.03 - i * 0.001, 4),
            "text": f"{doc} {section} " + "x" * 900,
        }
        for i, doc in enumerate(docs)
        for section in ("Symptoms", "Diagnose")
    ]
    return json.dumps({"query": "q", "mode": "hybrid", "results": rows, "note": "background"})


# Who called whom: the PSP is slow, so payments and orders fail, and the gateway with them.
EDGES = [
    {"from": "user", "to": "gateway", "rps": 14.1, "failed_ratio": 0.18, "p95_s": 0.2},
    {"from": "gateway", "to": "orders", "rps": 6.0, "failed_ratio": 0.31, "p95_s": 0.12},
    {"from": "gateway", "to": "inventory", "rps": 8.0, "failed_ratio": 0.0, "p95_s": 0.01},
    {"from": "orders", "to": "payments", "rps": 4.0, "failed_ratio": 0.3, "p95_s": 2.4},
    {"from": "orders", "to": "inventory", "rps": 4.0, "failed_ratio": 0.0, "p95_s": 0.02},
    {"from": "payments", "to": "psp", "rps": 4.0, "failed_ratio": 0.29, "p95_s": 2.3},
]
GRAPH = json.dumps({"window": "last 5 min", "edges": EDGES})


class KnowledgeBase(FakeToolbox):
    """A toolbox whose runbooks__search answers by kind, with the service graph."""

    def __init__(self, runbooks: str, postmortems: str, outputs: dict[str, str] | None = None):
        super().__init__({"runbooks__search": "", "metrics__service_dependencies": GRAPH, **(outputs or {})})
        self.by_kind = {"runbook": runbooks, "postmortem": postmortems}

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        if name != "runbooks__search":
            return await super().call(name, arguments)
        self.calls.append((name, arguments))
        return ToolOutcome(self.by_kind[arguments["kind"]], latency_ms=5)


def knowledge(outputs: dict[str, str] | None = None) -> KnowledgeBase:
    return KnowledgeBase(found("runbook", "a", "b", "c", "d"), found("postmortem", "INC-1"), outputs)


class Down:
    name = "down"

    async def complete(self, request: LLMRequest) -> Any:
        raise RuntimeError("the daily quota is used up")


def test_the_query_is_what_the_alerts_say() -> None:
    assert query_for(INCIDENT) == "HighErrorRate orders orders: 62% of requests are failing"


async def test_search_keeps_the_best_passage_of_the_three_top_documents_of_each_kind() -> None:
    kb = knowledge()
    related, problems = await search(kb, "q")
    assert [r.doc_id for r in related] == ["runbook:a", "runbook:b", "runbook:c", "postmortem:INC-1"]
    assert {r.section for r in related} == {"Symptoms"}  # the best passage of each document
    assert len(related[0].text) == PASSAGE_CHARS and problems == []
    assert sorted(a["kind"] for _, a in kb.calls) == ["postmortem", "runbook"]


async def test_a_missing_or_failing_knowledge_base_is_reported_not_raised() -> None:
    assert await search(FakeToolbox({"logs__error_summary": LOGS}), "q") == (
        [],
        ["the knowledge base is unavailable"],
    )
    broken = KnowledgeBase(json.dumps({"error": "knowledge base unavailable: OperationalError"}), "not json")
    related, problems = await search(broken, "q")
    assert related == []
    assert problems == [
        "runbook search: knowledge base unavailable: OperationalError",
        "postmortem search: not json",
    ]


def test_the_blast_radius_follows_the_calls_both_ways() -> None:
    graph = ServiceGraph(EDGES)
    assert graph.around({"orders"}) == (["gateway", "user"], ["inventory", "payments", "psp"])
    assert graph.around({"gateway", "orders"}) == (["user"], ["inventory", "payments", "psp"])
    assert graph.around({"psp"}) == (["gateway", "orders", "payments", "user"], [])
    assert [(e["from"], e["to"]) for e in graph.troubled()] == [
        ("user", "gateway"),
        ("gateway", "orders"),
        ("orders", "payments"),
        ("payments", "psp"),
    ]


async def test_the_model_reads_the_alerts_and_the_passages_and_assesses() -> None:
    provider = ReplayProvider([triage_turn({**TRIAGE, "leads": ["one", "two", "three", "four"]})])
    triage = await Triager(provider, knowledge(), ROUTE).triage(INCIDENT)

    assert triage.assessment is not None
    assert (triage.assessment.severity, triage.assessment.service) == ("critical", "orders")
    assert triage.assessment.leads == ["one", "two", "three"]  # at most three
    [request] = provider.requests
    assert (request.role, request.answer_now, request.tools) == ("triage", True, [])
    brief = request.messages[0]["content"]
    assert "orders: 62% of requests are failing" in brief and 'doc_id="postmortem:INC-1"' in brief
    assert "- orders -> payments: 4.0 req/s, 30% failed, p95 2.4 s" in brief
    assert "Callers of the alerting services (who feels it): gateway, user." in brief
    assert "What they call (where it may come from): inventory, payments, psp." in brief

    notes = triage.notes()
    assert notes is not None
    assert "Severity critical. Start with orders: Most checkouts fail" in notes
    assert "- Lead: one" in notes and "- runbook:a: a (Symptoms)" in notes
    assert "Downstream of the alerting services: inventory, payments, psp." in notes
    assert "- payments -> psp: 4.0 req/s, 29% failed, p95 2.3 s" in notes
    assert "gateway -> inventory" not in notes  # healthy calls stay out of the brief

    event = triage.event()
    assert len(event["dependencies"]) == 6
    assert (event["upstream"], event["downstream"]) == (["gateway", "user"], ["inventory", "payments", "psp"])


def test_look_alike_letters_are_folded_and_a_runaway_lead_is_dropped() -> None:
    from relay_agent.graph.triage import Assessment

    looping = "Check the gateway " + "content-" * 200
    a = Assessment(
        severity="warning", service="orders", summary="x" * 900, leads=["timed out (𝗌𝖾𝖾 𝖻𝖾𝗅𝗈𝗐)", looping]
    )
    assert a.leads == ["timed out (see below)"] and len(a.summary) == 400


async def test_without_the_model_the_documents_still_help() -> None:
    triage = await Triager(Down(), knowledge(), ROUTE).triage(INCIDENT)
    assert triage.assessment is None and len(triage.related) == 4
    assert triage.errors == ["model: RuntimeError: the daily quota is used up"]
    assert triage.downstream == ["inventory", "payments", "psp"]  # the graph needs no model
    notes = triage.notes()
    assert notes is not None and "Severity" not in notes and "runbook:a" in notes
    assert triage.event()["severity"] is None and triage.event()["model"] is None

    garbled = await Triager(ReplayProvider([answer_turn({"no": "assessment"})]), knowledge(), ROUTE).triage(
        INCIDENT
    )
    assert garbled.assessment is None and garbled.errors == ["model: the answer did not parse"]
    assert garbled.event()["model"]  # the call happened, and counts


async def test_with_nothing_found_and_no_answer_there_are_no_notes() -> None:
    triage = await Triager(Down(), FakeToolbox({"logs__error_summary": LOGS}), ROUTE).triage(INCIDENT)
    assert triage.notes() is None


def test_the_example_events_match_the_contract() -> None:
    for line in (EXAMPLES / "triage-events.jsonl").read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        assert errors("agent-event", event) == [], event["type"]


async def test_replayed_answers_go_to_the_agent_that_recorded_them() -> None:
    triage, first, second = triage_turn(), answer_turn({"n": 1}), answer_turn({"n": 2})
    provider = ReplayProvider([triage, first, second], roles=["triage", "investigator", "investigator"])

    def ask(role: str) -> LLMRequest:
        return LLMRequest(role=role, model="m", system="", messages=[])

    assert await provider.complete(ask("investigator")) is first  # the triage answer waits for triage
    assert await provider.complete(ask("triage")) is triage
    assert await provider.complete(ask("investigator")) is second
    with pytest.raises(ReplayExhausted):
        await provider.complete(ask("reviewer"))


# ------------------------------------------------------------ inside a run
def tools_of(toolbox: FakeToolbox) -> Any:
    @asynccontextmanager
    async def tools():
        yield toolbox

    return tools


async def test_a_run_starts_with_triage_and_the_investigator_reads_its_notes() -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    kb = knowledge({"logs__error_summary": LOGS, "metrics__service_health": METRICS})
    provider = ReplayProvider(
        [
            triage_turn(),
            tool_turn(("logs__error_summary", {"minutes": 15}), ("metrics__service_health", {})),
            answer_turn(report()),
        ]
    )
    settings = Settings(_env_file=None, reviewer_enabled=False)
    state = await Runner(settings, redis, provider, toolbox=tools_of(kb))(INCIDENT, "run-triage")
    assert state["status"] == "concluded"

    events = [json.loads(fields["event"]) for _, fields in await redis.xrange("relay.agent-events")]
    assert [e["seq"] for e in events] == list(range(len(events)))  # one sequence across agents
    assert [(e["agent"], e["type"]) for e in events[:3]] == [
        ("triage", "llm.completed"),
        ("triage", "triage.completed"),
        ("investigator", "run.started"),
    ]
    assert events[1]["data"]["severity"] == "critical" and len(events[1]["data"]["related"]) == 4
    for event in events:
        assert errors("agent-event", event) == [], event["type"]
    brief = provider.requests[1].messages[0]["content"]
    assert "<triage>" in brief and "Start with orders" in brief and "postmortem:INC-1" in brief


async def test_a_failed_triage_never_stops_the_investigation() -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    kb = knowledge({"logs__error_summary": LOGS, "metrics__service_health": METRICS})
    responses = [
        tool_turn(("logs__error_summary", {"minutes": 15}), ("metrics__service_health", {})),
        answer_turn(report()),
    ]
    # Scripted for the investigator only: triage finds no answer of its own.
    provider = ReplayProvider(responses, roles=["investigator", "investigator"])
    settings = Settings(_env_file=None, reviewer_enabled=False)
    state = await Runner(settings, redis, provider, toolbox=tools_of(kb))(INCIDENT, "run-no-triage-answer")

    assert state["status"] == "concluded"
    events = [json.loads(fields["event"]) for _, fields in await redis.xrange("relay.agent-events")]
    triaged = events[0]
    assert triaged["type"] == "triage.completed" and triaged["data"]["severity"] is None
    assert triaged["data"]["errors"][0].startswith("model: ReplayExhausted")
    assert "runbook:a" in provider.requests[1].messages[0]["content"]  # the documents still help


async def test_triage_can_be_turned_off() -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    toolbox = FakeToolbox({"logs__error_summary": LOGS, "metrics__service_health": METRICS})
    provider = ReplayProvider(
        [
            tool_turn(("logs__error_summary", {"minutes": 15}), ("metrics__service_health", {})),
            answer_turn(report()),
        ]
    )
    settings = Settings(_env_file=None, reviewer_enabled=False, triage_enabled=False)
    await Runner(settings, redis, provider, toolbox=tools_of(toolbox))(INCIDENT, "run-untriaged")
    kinds = [json.loads(fields["event"])["type"] for _, fields in await redis.xrange("relay.agent-events")]
    assert kinds[0] == "run.started" and "triage.completed" not in kinds
    assert "<triage>" not in provider.requests[0].messages[0]["content"]
