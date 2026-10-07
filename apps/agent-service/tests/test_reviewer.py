"""The reviewer: what it reads, how its verdicts change the ranking, and its place in a run."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import fakeredis
import pytest
from relay_agent.config import ModelRoute, Settings
from relay_agent.graph.reviewer import HypothesisReview, Review, Reviewer, apply, brief, excerpt
from relay_agent.graph.schemas import Alert, Incident
from relay_agent.llm.replay import ReplayProvider
from relay_agent.service.runner import Runner
from support import LOGS, METRICS, FakeToolbox, answer_turn, report, tool_turn, triage_turn
from test_contracts import errors

pytestmark = pytest.mark.anyio

INCIDENT = Incident(
    id="5d0c7a0e-0000-4000-8000-000000000002",
    number=31,
    title="HighErrorRate on orders",
    opened_at="2026-10-05T10:00:00Z",
    alerts=[Alert(name="HighErrorRate", service="orders", summary="orders: 40% of requests are failing")],
)


def two_hypotheses() -> dict[str, Any]:
    r = report()
    second = {
        **r["hypotheses"][0],
        "category": "db_pool_exhaustion",
        "service": "orders",
        "confidence": 0.6,
        "evidence": [{"evidence_id": "E2", "quote": "p95_s", "shows": "latency"}],
    }
    r["hypotheses"] = [{**r["hypotheses"][0], "confidence": 0.95}, second]
    return r


def review(*entries: tuple[int, str, float]) -> Review:
    return Review(
        reviews=[HypothesisReview(rank=r, verdict=v, confidence=c, reason="because") for r, v, c in entries]
    )


def test_confidence_only_goes_down_and_supported_hypotheses_move_ahead() -> None:
    outcomes = apply(two_hypotheses(), review((0, "unsupported", 0.99), (1, "supported", 0.4)))
    assert [o.original_rank for o in outcomes] == [1, 0]  # the supported one is now on top
    assert [o.hypothesis["confidence"] for o in outcomes] == [0.4, 0.95]  # min(stated, reviewed)
    assert [o.verdict for o in outcomes] == ["supported", "unsupported"]


def test_same_verdicts_keep_the_order_by_confidence_and_a_skipped_hypothesis_is_weak() -> None:
    outcomes = apply(two_hypotheses(), review((0, "weak", 0.7)))
    assert [(o.original_rank, o.verdict, o.hypothesis["confidence"]) for o in outcomes] == [
        (0, "weak", 0.7),
        (1, "weak", 0.6),
    ]


def test_the_brief_shows_each_quote_in_context_and_says_which_ones_hold() -> None:
    r = report(quote="slow response from psp", evidence_id="E1")
    r["hypotheses"][0]["evidence"].append({"evidence_id": "E3", "quote": "never said", "shows": "x"})
    evidence = {
        "E1": {"tool": "logs__error_summary", "arguments": {"minutes": 15}, "text": LOGS},
        "E3": {"tool": "runbooks__search", "arguments": {}, "text": "runbook text"},
    }
    checks = [
        {"hypothesis": 0, "evidence_id": "E1", "quote": "slow response from psp", "verdict": "verified"},
        {"hypothesis": 0, "evidence_id": "E3", "quote": "never said", "verdict": "reference_not_evidence"},
    ]
    text = brief(INCIDENT, r, evidence, checks)
    assert "orders: 40% of requests are failing" in text
    assert "E1 (quote found)" in text and '"pattern":"slow response from psp"' in text
    assert "E3 (reference, not evidence (a runbook or postmortem))" in text
    assert "E1 logs__error_summary, E3 runbooks__search" in text


def test_excerpts_center_on_the_quote() -> None:
    text = "a" * 2000 + "THE QUOTE" + "b" * 2000
    snippet, found = excerpt(text, "THE QUOTE")
    assert found and "THE QUOTE" in snippet and len(snippet) < 800
    assert excerpt(text, "absent") == (text[:700], False)


async def test_an_unparseable_review_is_no_review() -> None:
    provider = ReplayProvider([answer_turn({"not": "a review"})])
    result, response = await Reviewer(provider, ModelRoute(model="gemini-3.6-flash")).review(
        INCIDENT, two_hypotheses(), {}, []
    )
    assert result is None and response.model
    assert provider.requests[0].role == "reviewer" and provider.requests[0].answer_now


# ------------------------------------------------------------ inside a run
async def test_a_run_publishes_the_review_and_finishes() -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    toolbox = FakeToolbox({"logs__error_summary": LOGS, "metrics__service_health": METRICS})
    verdict = {"reviews": [{"rank": 0, "verdict": "weak", "confidence": 0.7, "reason": "one log pattern"}]}
    provider = ReplayProvider(
        [
            triage_turn(),
            tool_turn(("logs__error_summary", {"minutes": 15}), ("metrics__service_health", {})),
            answer_turn(report()),
            answer_turn(verdict),
        ]
    )

    @asynccontextmanager
    async def tools():
        yield toolbox

    await Runner(Settings(), redis, provider, toolbox=tools)(INCIDENT, "run-review")
    events = [json.loads(fields["event"]) for _, fields in await redis.xrange("relay.agent-events")]

    assert [e["seq"] for e in events] == list(range(len(events)))  # one sequence across agents
    assert [e["type"] for e in events][-3:] == ["llm.completed", "review.completed", "run.finished"]
    reviewed = events[-2]
    assert (reviewed["agent"], reviewed["data"]["order"]) == ("reviewer", [0])
    assert reviewed["data"]["reviews"][0] | {"reason": ""} == {
        "rank": 0,
        "verdict": "weak",
        "confidence": 0.7,
        "original_confidence": 0.86,
        "reason": "",
    }
    for event in events:
        assert errors("agent-event", event) == [], event["type"]


async def test_without_a_reviewer_the_verdict_stands() -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    toolbox = FakeToolbox({"logs__error_summary": LOGS, "metrics__service_health": METRICS})
    provider = ReplayProvider(
        [
            triage_turn(),
            tool_turn(("logs__error_summary", {"minutes": 15}), ("metrics__service_health", {})),
            answer_turn(report()),
        ]
    )

    @asynccontextmanager
    async def tools():
        yield toolbox

    await Runner(Settings(reviewer_enabled=False), redis, provider, toolbox=tools)(INCIDENT, "run-plain")
    kinds = [json.loads(fields["event"])["type"] for _, fields in await redis.xrange("relay.agent-events")]
    assert "review.completed" not in kinds and kinds[-1] == "run.finished"
    assert [r.role for r in provider.requests] == ["triage", "investigator", "investigator"]  # no reviewer
