"""Graph mechanics with a scripted model and fake tools — no network."""

from itertools import pairwise

import anyio
from relay_agent.llm.types import LLMResponse, Usage
from support import (
    INCIDENT,
    FakeToolbox,
    answer_turn,
    make_investigator,
    report,
    spec,
    tool_turn,
)


def run(investigator):
    return anyio.run(investigator.run, INCIDENT)


def test_investigation_concludes_with_verified_citations(toolbox: FakeToolbox) -> None:
    investigator, _, sink = make_investigator(
        [
            tool_turn(("logs__error_summary", {"minutes": 15}), ("metrics__service_health", {})),
            answer_turn(report()),
        ],
        toolbox,
    )
    state = run(investigator)

    assert state["status"] == "concluded"
    assert state["report"]["hypotheses"][0]["category"] == "dependency_latency"
    assert state["checks"] == [
        {"hypothesis": 0, "evidence_id": "E1", "quote": "slow response from psp", "verdict": "verified"}
    ]
    assert [e["id"] for e in state["evidence"]] == ["E1", "E2"]
    assert state["tool_calls"] == 2 and state["llm_calls"] == 2
    assert [name for name, _ in toolbox.calls] == ["logs__error_summary", "metrics__service_health"]
    assert sink.of("agent.progress")[0]["text"] == "Checking logs and metrics first."


def test_conversation_is_append_only(toolbox: FakeToolbox) -> None:
    """Each request's history must be a prefix of the next one: that is what keeps
    Claude's thinking blocks valid and the prompt cache hitting."""
    investigator, provider, _ = make_investigator(
        [
            tool_turn(("logs__error_summary", {})),
            tool_turn(("metrics__service_health", {})),
            answer_turn(report()),
        ],
        toolbox,
    )
    run(investigator)

    histories = [r.messages for r in provider.requests]
    assert len(histories) == 3
    for earlier, later in pairwise(histories):
        assert later[: len(earlier)] == earlier
    # Assistant turns are replayed exactly as returned, thinking blocks and signatures included.
    assert histories[1][1]["content"][0] == {
        "type": "thinking",
        "thinking": "Checking logs and metrics first.",
        "signature": "sig-1",
    }
    # The tool list never changes during a run.
    assert all(r.tools == provider.requests[0].tools for r in provider.requests)


def test_tool_results_are_wrapped_as_untrusted(toolbox: FakeToolbox) -> None:
    investigator, provider, _ = make_investigator(
        [tool_turn(("logs__error_summary", {})), answer_turn(report())], toolbox
    )
    run(investigator)
    result = provider.requests[1].messages[-1]["content"][0]
    assert result["type"] == "tool_result"
    assert result["content"].startswith(
        '<tool_output evidence_id="E1" tool="logs__error_summary" trust="untrusted">'
    )


def test_tool_budget_is_enforced(toolbox: FakeToolbox) -> None:
    investigator, provider, sink = make_investigator(
        [tool_turn(("logs__error_summary", {}), ("metrics__service_health", {})), answer_turn(report())],
        toolbox,
        tool_budget=1,
    )
    state = run(investigator)

    assert state["tool_calls"] == 1
    assert len(toolbox.calls) == 1
    results = provider.requests[1].messages[-1]["content"]
    assert results[1] == {
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": "Tool budget exhausted; this call was not executed.",
        "is_error": True,
    }
    assert results[-1]["type"] == "text" and "budget exhausted" in results[-1]["text"]
    assert sink.of("budget.exhausted")
    # Once the budget is spent the provider is told the answer is due now.
    assert [r.answer_now for r in provider.requests] == [False, True]


def test_write_tools_are_never_exposed_to_the_investigator() -> None:
    toolbox = FakeToolbox(
        {"logs__error_summary": "{}", "github__create_pull_request": "{}"},
        specs=[spec("logs__error_summary"), spec("github__create_pull_request", read_only=False)],
    )
    investigator, provider, _ = make_investigator(
        [
            tool_turn(("github__create_pull_request", {"title": "revert"})),
            answer_turn(report(evidence_id="E9")),
        ],
        toolbox,
    )
    state = run(investigator)

    assert [t["name"] for t in provider.requests[0].tools] == ["logs__error_summary"]
    assert toolbox.calls == []  # the call was refused, not executed
    rejected = provider.requests[1].messages[-1]["content"][0]
    assert rejected["is_error"] and "not available" in rejected["content"]
    assert state["checks"][0]["verdict"] == "unknown_evidence"


def test_a_missing_report_is_nudged_then_accepted(toolbox: FakeToolbox) -> None:
    rambling = LLMResponse(
        [{"type": "text", "text": "I think it is the PSP."}], "end_turn", "claude-opus-5-5", Usage(), 0.0, 10
    )
    investigator, provider, _ = make_investigator(
        [tool_turn(("logs__error_summary", {})), rambling, answer_turn(report())], toolbox
    )
    state = run(investigator)

    assert state["status"] == "concluded"
    assert state["nudges"] == 1
    assert provider.requests[2].messages[-1]["role"] == "user"
    assert [r.answer_now for r in provider.requests] == [False, False, True]


def test_hallucinated_quotes_are_flagged(toolbox: FakeToolbox) -> None:
    investigator, _, _ = make_investigator(
        [tool_turn(("logs__error_summary", {})), answer_turn(report(quote="PSP returned HTTP 500"))], toolbox
    )
    state = run(investigator)
    assert state["checks"][0]["verdict"] == "quote_not_found"


def test_refusal_stops_the_run(toolbox: FakeToolbox) -> None:
    refusal = LLMResponse([], "refusal", "claude-opus-5-5", Usage(), 0.0, 10, refusal_category="cyber")
    investigator, _, sink = make_investigator([refusal], toolbox)
    state = run(investigator)
    assert state["status"] == "refused"
    assert "cyber" in state["error"]
    assert sink.of("run.failed")


def test_costs_and_tokens_accumulate(toolbox: FakeToolbox) -> None:
    first, second = tool_turn(("logs__error_summary", {})), answer_turn(report())
    first.cost_usd, second.cost_usd = 0.04, 0.02
    investigator, _, _ = make_investigator([first, second], toolbox)
    state = run(investigator)
    assert abs(state["cost_usd"] - 0.06) < 1e-9
    assert state["usage"]["cache_read_tokens"] == 5200
    assert state["usage"]["output_tokens"] == 750
