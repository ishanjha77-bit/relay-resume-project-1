from relay_agent.graph.schemas import InvestigationReport, report_schema
from relay_agent.llm.anthropic_provider import (
    FALLBACK_BETA,
    THINKING_BINDING_BETA,
    THINKING_UPDATES_BETA,
    AnthropicProvider,
)
from relay_agent.llm.pricing import cost_usd
from relay_agent.llm.types import LLMRequest, Usage


def request(model: str) -> LLMRequest:
    return LLMRequest(
        role="investigator",
        model=model,
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "logs__error_summary", "description": "d", "input_schema": {"type": "object"}}],
        output_schema={"type": "object"},
        effort="medium",
        max_tokens=16000,
    )


def test_opus_request_uses_adaptive_thinking_caching_and_fallbacks() -> None:
    params, betas = AnthropicProvider(api_key="test").build_params(request("claude-opus-5-5"))
    assert params["thinking"] == {
        "type": "adaptive",
        "display": "updates",
        "block_binding": {"prefix_mismatch_behavior": "error"},
    }
    assert params["output_config"] == {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": {"type": "object"}},
    }
    assert params["fallbacks"] == "default"
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["cache_control"] == {"type": "ephemeral"}
    assert "tool_choice" not in params  # forced tool choice is a 400 on Opus 5.5
    assert set(betas) == {THINKING_UPDATES_BETA, THINKING_BINDING_BETA, FALLBACK_BETA}


def test_haiku_request_omits_unsupported_parameters() -> None:
    params, betas = AnthropicProvider(api_key="test").build_params(request("claude-haiku-4-5"))
    assert "thinking" not in params
    assert "effort" not in params["output_config"]
    assert "fallbacks" not in params
    assert betas == []


def test_opus_5_5_cost_uses_its_own_cache_read_price() -> None:
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_write_5m_tokens=200_000,
        cache_read_tokens=2_000_000,
    )
    # 1M x $4 + 0.1M x $20 + 0.2M x $5 + 2M x $0.20 = 4 + 2 + 1 + 0.4
    assert abs(cost_usd("claude-opus-5-5", usage) - 7.4) < 1e-9


def test_unknown_models_cost_nothing() -> None:
    assert cost_usd("llama-local", Usage(input_tokens=10_000)) == 0.0


def test_dated_snapshot_ids_are_priced() -> None:
    # The API reports claude-haiku-4-5 as its dated snapshot.
    assert cost_usd("claude-haiku-4-5-20251001", Usage(output_tokens=1_000_000)) == 5.0


def test_report_schema_is_strict_and_bounds_are_enforced_client_side() -> None:
    schema = report_schema()
    assert schema["additionalProperties"] is False
    defs = schema.get("$defs", {})
    assert defs["Hypothesis"]["additionalProperties"] is False
    confidence = defs["Hypothesis"]["properties"]["confidence"]
    assert "minimum" not in confidence and "maximum" not in confidence  # moved into the description
    bad = {
        "summary": "s",
        "impact": "i",
        "affected_services": [],
        "started_at": None,
        "hypotheses": [
            {
                "category": "unknown",
                "service": "x",
                "component": "y",
                "summary": "z",
                "confidence": 1.7,
                "evidence": [],
                "suggested_fix": "f",
            }
        ],
        "injection_suspected": False,
        "injection_evidence_ids": [],
    }
    try:
        InvestigationReport.model_validate(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("confidence > 1 must be rejected")
