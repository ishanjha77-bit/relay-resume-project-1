"""The Gemini adapter against real google-genai types and a scripted client — no network."""

import base64
import json
from typing import Any

import httpx
import pytest
from google.genai import errors, types
from pydantic import SecretStr
from relay_agent.config import Budget, ModelRoute, Settings
from relay_agent.events import CollectingSink
from relay_agent.graph.investigator import Investigator
from relay_agent.graph.schemas import report_schema
from relay_agent.llm.factory import MissingCredentials, make_provider
from relay_agent.llm.gemini_provider import (
    DROPPED_CALL,
    FOREIGN_SIGNATURE,
    RAW_PARTS,
    REPORT_INSTRUCTION,
    REPORT_NOT_ACCEPTED,
    REPORT_TOOL,
    GeminiProvider,
    QuotaExhausted,
    build_request,
    history_model,
    inline_refs,
    quota_of,
)
from relay_agent.llm.types import LLMRequest, Usage
from support import INCIDENT, FakeToolbox, report

pytestmark = pytest.mark.anyio

MODEL = "gemini-3.8-flash"


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def thought(text: str) -> dict[str, Any]:
    return {"text": text, "thought": True}


def call(name: str, args: dict[str, Any], signature: str | None = None) -> dict[str, Any]:
    part: dict[str, Any] = {"function_call": {"name": name, "args": args}}
    if signature:
        part["thought_signature"] = b64(signature)
    return part


def gemini(*parts: dict[str, Any], finish: str = "STOP", model: str = MODEL) -> types.GenerateContentResponse:
    return types.GenerateContentResponse.model_validate(
        {
            "candidates": [{"content": {"role": "model", "parts": list(parts)}, "finish_reason": finish}],
            "usage_metadata": {
                "prompt_token_count": 1000,
                "cached_content_token_count": 200,
                "candidates_token_count": 50,
                "thoughts_token_count": 100,
            },
            "model_version": model,
            "response_id": "resp-1",
        }
    )


class FakeModels:
    """Serves scripted responses in order; an exception in the script is raised instead."""

    def __init__(self, responses: list[types.GenerateContentResponse | Exception]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def generate_content(
        self, *, model: str, contents: Any, config: Any
    ) -> types.GenerateContentResponse:
        self.calls.append({"model": model, "contents": contents, "config": config})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeAio:
    def __init__(self, models: FakeModels):
        self.models = models

    async def aclose(self) -> None:
        pass


class FakeClient:
    def __init__(self, responses: list[types.GenerateContentResponse | Exception]):
        self.models = FakeModels(responses)
        self.aio = FakeAio(self.models)


def provider(*responses: types.GenerateContentResponse | Exception) -> tuple[GeminiProvider, FakeModels]:
    client = FakeClient(list(responses))
    return GeminiProvider(client=client), client.models


TOOLS = [
    {"name": "logs__error_summary", "description": "error patterns", "input_schema": {"type": "object"}},
    {"name": "metrics__service_health", "description": "RED snapshot", "input_schema": {"type": "object"}},
]


def request(
    messages: list[dict[str, Any]] | None = None, model: str = MODEL, fallbacks: list[str] | None = None
) -> LLMRequest:
    return LLMRequest(
        role="investigator",
        model=model,
        system="You are Relay.",
        messages=messages or [{"role": "user", "content": "Investigate INC-7."}],
        tools=TOOLS,
        output_schema=report_schema(),
        effort="medium",
        fallbacks=fallbacks or [],
    )


def busy() -> errors.ServerError:
    return errors.ServerError(
        503,
        {
            "error": {
                "code": 503,
                "message": "This model is currently experiencing high demand.",
                "status": "UNAVAILABLE",
            }
        },
    )


async def test_tool_calls_come_back_as_anthropic_shaped_blocks() -> None:
    gem, _ = provider(
        gemini(
            thought("Orders is failing: logs and metrics first."),
            call("logs__error_summary", {"minutes": 15}, signature="sig-1"),
            call("metrics__service_health", {}),
        )
    )
    response = await gem.complete(request())

    assert response.stop_reason == "tool_use"
    assert [b["type"] for b in response.content] == ["thinking", "tool_use", "tool_use", RAW_PARTS]
    assert response.progress_notes() == ["Orders is failing: logs and metrics first."]
    assert [(t["name"], t["input"]) for t in response.tool_uses()] == [
        ("logs__error_summary", {"minutes": 15}),
        ("metrics__service_health", {}),
    ]
    assert response.usage == Usage(input_tokens=800, cache_read_tokens=200, output_tokens=150)
    assert response.cost_usd == 0.0  # free tier: tokens counted, nothing billed
    assert (response.model, response.request_id) == (MODEL, "resp-1")


async def test_the_report_call_arrives_as_the_json_text_the_investigator_parses() -> None:
    gem, _ = provider(gemini(call(REPORT_TOOL, report()), call("logs__error_summary", {"minutes": 5})))
    response = await gem.complete(request())

    assert response.stop_reason == "end_turn"
    assert response.tool_uses() == []  # answering, not investigating
    assert json.loads(response.text()) == report()


def test_requests_carry_tools_the_report_function_and_thinking() -> None:
    contents, config = build_request(request())

    names = [d.name for d in config.tools[0].function_declarations]
    assert names == ["logs__error_summary", "metrics__service_health", REPORT_TOOL]
    report_params = config.tools[0].function_declarations[-1].parameters_json_schema
    assert "$ref" not in json.dumps(report_params) and "$defs" not in report_params
    assert config.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.VALIDATED
    assert config.system_instruction.endswith(REPORT_INSTRUCTION)
    assert config.thinking_config.thinking_level == types.ThinkingLevel.MEDIUM
    assert config.thinking_config.include_thoughts
    assert config.automatic_function_calling.disable
    assert [(c.role, c.parts[0].text) for c in contents] == [("user", "Investigate INC-7.")]

    _, legacy = build_request(request(model="gemini-2.5-flash"))
    assert (legacy.thinking_config.thinking_level, legacy.thinking_config.thinking_budget) == (None, 8192)


async def test_history_resends_the_model_turn_exactly_and_names_each_result() -> None:
    gem, models = provider(
        gemini(
            call("logs__error_summary", {"minutes": 15}, signature="sig-1"),
            call("metrics__service_health", {}),
        ),
        gemini(call(REPORT_TOOL, report())),
    )
    first = await gem.complete(request())
    calls = first.tool_uses()
    messages = [
        {"role": "user", "content": "Investigate INC-7."},
        {"role": "assistant", "content": first.content},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": calls[0]["id"],
                    "content": "<tool_output>E1</tool_output>",
                },
                {"type": "tool_result", "tool_use_id": calls[1]["id"], "content": "boom", "is_error": True},
                {"type": "text", "text": "Tool budget exhausted."},
            ],
        },
    ]
    await gem.complete(
        request(json.loads(json.dumps(messages)))
    )  # history survives JSON (checkpoints, recordings)

    user, model, results = models.calls[1]["contents"]
    assert (user.role, model.role, results.role) == ("user", "model", "user")
    assert model.parts[0].thought_signature == b"sig-1"
    responses = [p.function_response for p in results.parts if p.function_response]
    assert [(r.name, r.response) for r in responses] == [
        ("logs__error_summary", {"output": "<tool_output>E1</tool_output>"}),
        ("metrics__service_health", {"error": "boom"}),
    ]
    assert results.parts[-1].text == "Tool budget exhausted."


async def test_a_rejected_report_and_its_dropped_calls_are_answered_before_the_reminder() -> None:
    gem, models = provider(
        gemini(call(REPORT_TOOL, {"summary": "incomplete"}), call("logs__error_summary", {"minutes": 5})),
        gemini(call(REPORT_TOOL, report())),
    )
    rejected = await gem.complete(request())
    messages = [
        {"role": "user", "content": "Investigate INC-7."},
        {"role": "assistant", "content": rejected.content},
        {"role": "user", "content": "Reply with the JSON report now."},
    ]
    await gem.complete(request(messages))

    reply = models.calls[1]["contents"][-1]
    assert [(p.function_response.name, p.function_response.response) for p in reply.parts[:2]] == [
        (REPORT_TOOL, {"error": REPORT_NOT_ACCEPTED}),
        ("logs__error_summary", {"error": DROPPED_CALL}),
    ]
    assert reply.parts[2].text == "Reply with the JSON report now."


async def test_blocked_prompts_and_safety_stops_are_refusals() -> None:
    blocked = types.GenerateContentResponse.model_validate({"prompt_feedback": {"block_reason": "SAFETY"}})
    gem, _ = provider(blocked, gemini(finish="PROHIBITED_CONTENT"))

    first = await gem.complete(request())
    second = await gem.complete(request())
    assert (first.stop_reason, first.refusal_category) == ("refusal", "safety")
    assert (second.stop_reason, second.refusal_category) == ("refusal", "prohibited_content")


async def test_an_investigation_concludes_on_gemini(toolbox: FakeToolbox) -> None:
    gem, models = provider(
        gemini(
            thought("Checking payments' latency."),
            call("logs__error_summary", {"minutes": 15}, signature="sig-1"),
            call("metrics__service_health", {}),
        ),
        gemini(call(REPORT_TOOL, report())),
    )
    investigator = Investigator(
        gem, toolbox, ModelRoute(model=MODEL), Budget(tool_calls=15), CollectingSink()
    )

    state = await investigator.run(INCIDENT)

    assert state["status"] == "concluded"
    assert state["checks"][0]["verdict"] == "verified"
    assert [name for name, _ in toolbox.calls] == ["logs__error_summary", "metrics__service_health"]
    assert len(models.calls) == 2


def test_inline_refs_produces_a_self_contained_schema() -> None:
    schema = {
        "$defs": {
            "Leaf": {"type": "string", "enum": ["a", "b"]},
            "Node": {"type": "object", "properties": {"leaf": {"$ref": "#/$defs/Leaf"}}},
        },
        "type": "object",
        "properties": {"nodes": {"type": "array", "items": {"$ref": "#/$defs/Node"}}},
    }
    assert inline_refs(schema) == {
        "type": "object",
        "properties": {
            "nodes": {
                "type": "array",
                "items": {"type": "object", "properties": {"leaf": {"type": "string", "enum": ["a", "b"]}}},
            }
        },
    }


def test_the_provider_follows_from_the_model_name() -> None:
    assert ModelRoute(model="gemini-3.8-flash").provider == "gemini"
    assert ModelRoute(model="claude-opus-5-5").provider == "anthropic"
    assert ModelRoute(model="my-model", provider="gemini").provider == "gemini"
    with pytest.raises(ValueError, match="set provider"):
        ModelRoute(model="llama-4")


def test_a_missing_key_says_where_to_get_a_free_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.investigator.provider == "gemini"  # free by default
    with pytest.raises(MissingCredentials, match=r"aistudio\.google\.com"):
        make_provider(settings.investigator, settings)
    keyed = Settings(_env_file=None, gemini_api_key=SecretStr("test-key"))
    assert make_provider(keyed.investigator, keyed).name == "gemini"


# ------------------------------------------------------------ free-tier capacity


def quota_error(quota_id: str, retry_delay: str, limit: str = "20") -> errors.ClientError:
    """A 429 shaped like the Gemini API's."""
    return errors.ClientError(
        429,
        {
            "error": {
                "code": 429,
                "message": "You exceeded your current quota, please check your plan and billing details.",
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [
                            {
                                "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                                "quotaId": quota_id,
                                "quotaValue": limit,
                            }
                        ],
                    },
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay},
                ],
            }
        },
    )


def daily_quota() -> errors.ClientError:
    return quota_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier", "55941s")


def minute_quota() -> errors.ClientError:
    return quota_error("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "7s", limit="5")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def quota_provider(
    *responses: types.GenerateContentResponse | Exception,
) -> tuple[GeminiProvider, FakeModels, Clock]:
    client, clock = FakeClient(list(responses)), Clock()
    return GeminiProvider(client=client, clock=clock, sleep=clock.sleep, wall=clock), client.models, clock


def test_a_429_says_which_quota_ran_out() -> None:
    assert quota_of(daily_quota()) == quota_of(daily_quota())
    daily, minute = quota_of(daily_quota()), quota_of(minute_quota())
    assert (daily.daily, daily.retry_after_s, daily.limit) == (True, 55941.0, "20")
    assert (minute.daily, minute.retry_after_s) == (False, 7.0)
    assert quota_of(busy()) is None


async def test_a_busy_model_hands_the_run_to_the_next_one() -> None:
    gem, models = provider(
        busy(),
        httpx.ReadTimeout("no answer"),
        gemini(call("logs__error_summary", {"minutes": 15}), model="gemini-3.5-flash"),
    )
    response = await gem.complete(request(fallbacks=["gemini-3.6-flash", "gemini-3.5-flash"]))

    assert [c["model"] for c in models.calls] == [MODEL, "gemini-3.6-flash", "gemini-3.5-flash"]
    assert response.model == "gemini-3.5-flash"
    assert response.content[-1]["model"] == "gemini-3.5-flash"  # it wrote this turn
    # Only the last model left waits out a spike; the others give way quickly.
    timeouts = [c["config"].http_options.timeout for c in models.calls]
    assert timeouts[0] == timeouts[1] < timeouts[2]


async def test_a_run_continues_on_its_model_and_moves_on_when_it_cannot() -> None:
    gem, models = provider(
        gemini(thought("Orders first."), call("logs__error_summary", {"minutes": 15}, signature="sig-1")),
        busy(),  # the run's model is overloaded on the next turn...
        gemini(call(REPORT_TOOL, report()), model="gemini-3.5-flash"),  # ...so the next one finishes it
    )
    first = await gem.complete(request(fallbacks=["gemini-3.5-flash"]))
    history = [
        {"role": "user", "content": "Investigate INC-7."},
        {"role": "assistant", "content": first.content},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": first.tool_uses()[0]["id"], "content": "ok"}],
        },
    ]
    assert history_model(history) == MODEL

    final = await gem.complete(request(history, fallbacks=["gemini-3.5-flash"]))

    assert [c["model"] for c in models.calls] == [MODEL, MODEL, "gemini-3.5-flash"]
    own, foreign = models.calls[1]["contents"][1], models.calls[2]["contents"][1]
    assert own.parts[1].thought_signature == b"sig-1"  # its own turn, untouched
    # Another model's turn: thoughts dropped, the call marked as made elsewhere.
    assert [p.thought for p in foreign.parts] == [None]
    assert foreign.parts[0].thought_signature == FOREIGN_SIGNATURE
    assert json.loads(final.text()) == report()


async def test_a_model_out_of_daily_quota_is_skipped_until_it_resets() -> None:
    gem, models, clock = quota_provider(
        daily_quota(),
        gemini(call("logs__error_summary", {}), model="gemini-3.5-flash"),
        gemini(call("logs__error_summary", {}), model="gemini-3.5-flash"),
        gemini(call("logs__error_summary", {})),
    )
    await gem.complete(request(fallbacks=["gemini-3.5-flash"]))
    await gem.complete(request(fallbacks=["gemini-3.5-flash"]))  # no second try on the spent model
    assert [c["model"] for c in models.calls] == [MODEL, "gemini-3.5-flash", "gemini-3.5-flash"]

    clock.now += 55941  # midnight Pacific
    await gem.complete(request(fallbacks=["gemini-3.5-flash"]))
    assert models.calls[-1]["model"] == MODEL


async def test_a_spent_quota_comes_back_with_the_wall_clock_even_if_the_host_slept() -> None:
    # A laptop that sleeps through midnight Pacific: the monotonic clock barely moves.
    client, monotonic, wall = (
        FakeClient([daily_quota(), gemini(call("logs__error_summary", {}))]),
        Clock(),
        Clock(),
    )
    gem = GeminiProvider(client=client, clock=monotonic, sleep=monotonic.sleep, wall=wall)
    with pytest.raises(QuotaExhausted):
        await gem.complete(request())
    monotonic.now += 60
    wall.now += 55941 + 60
    await gem.complete(request())
    assert [c["model"] for c in client.models.calls] == [MODEL, MODEL]


async def test_when_every_model_is_out_of_quota_the_error_says_so() -> None:
    gem, models, _ = quota_provider(daily_quota(), daily_quota())
    with pytest.raises(QuotaExhausted, match="midnight Pacific time \\(in about 16 h\\)"):
        await gem.complete(request(fallbacks=["gemini-3.5-flash"]))
    with pytest.raises(QuotaExhausted):  # known spent: no request at all
        await gem.complete(request(fallbacks=["gemini-3.5-flash"]))
    assert len(models.calls) == 2


async def test_a_per_minute_limit_is_waited_out_on_the_same_model() -> None:
    gem, models, clock = quota_provider(minute_quota(), gemini(call("logs__error_summary", {})))
    await gem.complete(request(fallbacks=["gemini-3.5-flash"]))
    assert clock.slept == [7.0]
    assert [c["model"] for c in models.calls] == [MODEL, MODEL]


async def test_a_request_gemini_cancelled_moves_on_to_the_next_model() -> None:
    cancelled = errors.ClientError(
        499, {"error": {"code": 499, "message": "The operation was cancelled.", "status": "CANCELLED"}}
    )
    gem, models = provider(cancelled, gemini(call("logs__error_summary", {}), model="gemini-3.5-flash"))
    response = await gem.complete(request(fallbacks=["gemini-3.5-flash"]))
    assert [c["model"] for c in models.calls] == [MODEL, "gemini-3.5-flash"]
    assert response.model == "gemini-3.5-flash"


async def test_a_bad_request_is_not_retried_on_another_model() -> None:
    invalid = errors.ClientError(
        400, {"error": {"code": 400, "message": "bad schema", "status": "INVALID_ARGUMENT"}}
    )
    gem, models = provider(invalid)
    with pytest.raises(errors.ClientError):
        await gem.complete(request(fallbacks=["gemini-3.5-flash"]))
    assert len(models.calls) == 1


def test_history_from_another_provider_is_marked_as_foreign() -> None:
    claude_turn = [
        {"type": "tool_use", "id": "toolu_1", "name": "logs__error_summary", "input": {"minutes": 5}}
    ]
    contents, _ = build_request(
        request([{"role": "user", "content": "Go."}, {"role": "assistant", "content": claude_turn}])
    )
    assert contents[1].parts[0].thought_signature == FOREIGN_SIGNATURE


def test_when_the_answer_is_due_only_the_report_can_be_called() -> None:
    due = request()
    due.answer_now = True
    _, config = build_request(due)
    calling = config.tool_config.function_calling_config
    assert calling.mode == types.FunctionCallingConfigMode.ANY
    assert calling.allowed_function_names == [REPORT_TOOL]


def test_thinking_depth_is_set_the_way_each_model_family_accepts() -> None:
    from relay_agent.llm.gemini_provider import _thinking

    assert _thinking("gemini-3.6-flash", "medium").thinking_level is not None
    assert _thinking("gemini-2.5-flash", "medium").thinking_budget is not None
    gemma = _thinking("gemma-4-26b-a4b-it", "medium")
    assert (gemma.include_thoughts, gemma.thinking_level, gemma.thinking_budget) == (True, None, None)
