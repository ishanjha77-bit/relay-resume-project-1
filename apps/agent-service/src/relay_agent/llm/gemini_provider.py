"""Gemini via Google's google-genai SDK — Relay's default, because its free tier
makes the whole project free to run (see docs/adr/0010-free-by-default-gemini.md).

Relay's conversation is Anthropic-shaped (llm/types.py); this adapter
translates at the edge:

- Assistant turns come back as thinking / text / tool_use blocks, plus one
  ``gemini_parts`` block holding the model's raw parts and the model that
  wrote them. Gemini 3 binds its reasoning to function calls with thought
  signatures and rejects a tool-result turn whose signatures are missing, so
  later requests to that model resend those parts exactly as received.
- ``tool_result`` blocks become function responses, named after the call
  they answer.
- The final report is a ``submit_report`` function whose parameters are the
  report schema; ``VALIDATED`` function calling constrains every call's
  arguments to its schema. The call comes back as a text block holding the
  JSON, which is exactly what the investigator parses for any provider.
- Thought summaries (``include_thoughts``) are the agent's progress notes.
- The free tier allows each model a few requests a minute and about twenty a
  day. A run continues on the model that wrote its history while that model
  answers; when it can't (out of quota, overloaded, silent), the next model of
  the route takes over, with the earlier history marked as written elsewhere.
  Models out of daily quota are skipped until their quota resets.
- Free-tier calls cost nothing; tokens are still counted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from relay_agent.llm.pricing import cost_usd
from relay_agent.llm.types import LLMRequest, LLMResponse, Usage

log = logging.getLogger(__name__)

RAW_PARTS = "gemini_parts"
REPORT_TOOL = "submit_report"
REPORT_INSTRUCTION = f"""

## Submitting the report
To reply with the JSON report, call the `{REPORT_TOOL}` function with the report as its
arguments. It is not an investigation tool and does not count against your budget."""
REPORT_NOT_ACCEPTED = "Not accepted; see the message that follows."
DROPPED_CALL = "Not executed: the report was submitted in the same turn."

# Gemini accepts function calls it didn't make (another model's, or another
# provider's) when they carry this marker instead of a signature; without one
# it rejects the request. Checked against the API on 2026-10-05.
FOREIGN_SIGNATURE = b"skip_thought_signature_validator"

# Our ids for calls that came back without one; never sent to the API.
_LOCAL_ID = "relay-"

_THINKING_LEVEL = {
    "minimal": types.ThinkingLevel.MINIMAL,
    "low": types.ThinkingLevel.LOW,
    "medium": types.ThinkingLevel.MEDIUM,
    "high": types.ThinkingLevel.HIGH,
    "xhigh": types.ThinkingLevel.HIGH,
    "max": types.ThinkingLevel.HIGH,
}
# Gemini 2.x takes a token budget instead of a level.
_THINKING_BUDGET = {"minimal": 512, "low": 1024, "medium": 8192, "high": 24576, "xhigh": 24576, "max": 24576}

_STOP_REASONS = {
    types.FinishReason.STOP: "end_turn",
    types.FinishReason.MAX_TOKENS: "max_tokens",
    # A garbled call is not a refusal: the investigator asks for the report again.
    types.FinishReason.MALFORMED_FUNCTION_CALL: "end_turn",
    types.FinishReason.UNEXPECTED_TOOL_CALL: "end_turn",
    types.FinishReason.TOO_MANY_TOOL_CALLS: "end_turn",
}

# The SDK retries transient failures; 429s are left to us, because the error
# says whether a minute's or a day's quota ran out, and only one is worth waiting for.
# 499 is Gemini cancelling a request under load ("The operation was cancelled"):
# generating is side-effect free, so it is retried like the others.
_TRANSIENT = [408, 499, 500, 502, 503, 504]
# Another model can take over: try once more, then move on.
QUICK = types.HttpOptions(
    timeout=60_000,
    retry_options=types.HttpRetryOptions(attempts=2, initial_delay=2.0, http_status_codes=_TRANSIENT),
)
# The last model left: ride out a spike (backoff over about three minutes).
PATIENT = types.HttpOptions(
    timeout=180_000,
    retry_options=types.HttpRetryOptions(
        attempts=8, initial_delay=2.0, max_delay=60.0, exp_base=2.0, jitter=1.0, http_status_codes=_TRANSIENT
    ),
)
RATE_LIMIT_WAITS = 3  # per-minute 429s waited out per call, at most this many
MAX_RATE_LIMIT_WAIT_S = 65.0


class QuotaExhausted(RuntimeError):
    """Every model of the route is out of its free-tier daily quota."""


@dataclass(frozen=True)
class Quota:
    """What a 429 says ran out."""

    daily: bool
    retry_after_s: float | None
    limit: str | None


def quota_of(e: BaseException) -> Quota | None:
    if not isinstance(e, errors.APIError) or e.code != 429:
        return None
    found: dict[str, list[str]] = {"quotaId": [], "retryDelay": [], "quotaValue": []}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                camel = re.sub(r"_(\w)", lambda m: m.group(1).upper(), key)
                if camel in found and not isinstance(value, dict | list):
                    found[camel].append(str(value))
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(e.details)
    delay = re.match(r"([\d.]+)s$", found["retryDelay"][0]) if found["retryDelay"] else None
    return Quota(
        daily=any("PerDay" in q for q in found["quotaId"]),
        retry_after_s=float(delay.group(1)) if delay else None,
        limit=found["quotaValue"][0] if found["quotaValue"] else None,
    )


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        client: Any | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        wall: Callable[[], float] = time.time,
    ):
        self._client = client or genai.Client(api_key=api_key)
        self._clock = clock
        self._sleep = sleep
        # Models out of daily quota, and when (wall-clock time) their quota comes back. A
        # monotonic clock stops while a laptop sleeps, and would outlast the reset.
        self._wall = wall
        self._exhausted_until: dict[str, float] = {}

    async def aclose(self) -> None:
        await self._client.aio.aclose()

    async def complete(self, request: LLMRequest) -> LLMResponse:
        chain = list(dict.fromkeys([request.model, *request.fallbacks]))
        writer = history_model(request.messages)
        if writer:  # the model that wrote the history first: its signatures are its own
            chain = [writer, *(m for m in chain if m != writer)]
        models = [m for m in chain if self._exhausted_until.get(m, 0.0) <= self._wall()]
        if not models:
            raise QuotaExhausted(self._out_of_quota(chain))
        for i, model in enumerate(models):
            last = i == len(models) - 1
            try:
                return await self._call(request, model, PATIENT if last else QUICK)
            except Exception as e:
                quota = quota_of(e)
                if quota is not None and quota.daily:
                    self._exhausted_until[model] = self._wall() + (quota.retry_after_s or 3600.0)
                    if last:
                        raise QuotaExhausted(self._out_of_quota(chain)) from e
                elif last or not _unavailable(e):
                    raise
                log.warning("%s: %s; continuing on %s", model, _brief(e, quota), models[i + 1])
        raise AssertionError("unreachable: the last model returns or raises")

    async def _call(self, request: LLMRequest, model: str, http_options: types.HttpOptions) -> LLMResponse:
        contents, config = build_request(request, model=model, http_options=http_options)
        for waited in range(RATE_LIMIT_WAITS + 1):
            started = self._clock()
            try:
                response = await self._client.aio.models.generate_content(
                    model=model, contents=contents, config=config
                )
                break
            except errors.APIError as e:
                quota = quota_of(e)
                if quota is None or quota.daily or waited == RATE_LIMIT_WAITS:
                    raise
                delay = min(quota.retry_after_s or 10.0, MAX_RATE_LIMIT_WAIT_S)
                log.info("%s: per-minute limit reached; retrying in %.0f s", model, delay)
                await self._sleep(delay)
        latency_ms = int((self._clock() - started) * 1000)
        content, stop_reason, refusal = parse_response(response, model)
        served = response.model_version or model
        usage = _usage(response.usage_metadata)
        return LLMResponse(
            content=content,
            stop_reason=stop_reason,
            model=served,
            usage=usage,
            cost_usd=cost_usd(served, usage),
            latency_ms=latency_ms,
            request_id=response.response_id,
            refusal_category=refusal,
        )

    def _out_of_quota(self, chain: list[str]) -> str:
        now = self._wall()
        out = {m: until for m in chain if (until := self._exhausted_until.get(m, 0.0)) > now}
        others = [m for m in chain if m not in out]
        hours = (min(out.values()) - now) / 3600 if out else 0.0
        return (
            f"the Gemini free tier's daily quota is used up for {', '.join(out)}"
            + (f" and {', '.join(others)} did not answer" if others else "")
            + f"; quotas reset at midnight Pacific time (in about {hours:.0f} h). "
            "Add more models to the route's fallbacks, or try again then."
        )


def history_model(messages: list[dict[str, Any]]) -> str | None:
    """The model that wrote the latest turn of this conversation, if any."""
    for message in reversed(messages):
        if message["role"] == "assistant" and isinstance(message["content"], list):
            for block in message["content"]:
                if block.get("type") == RAW_PARTS and block.get("model"):
                    return block["model"]
    return None


def _unavailable(e: Exception) -> bool:
    """Busy, rate-limited, failing or silent: worth trying another model."""
    if isinstance(e, errors.APIError):
        return e.code in (429, *_TRANSIENT)
    return isinstance(e, httpx.TransportError)  # timeouts included


def _brief(e: Exception, quota: Quota | None) -> str:
    if quota is not None:
        scope = "daily" if quota.daily else "per-minute"
        return f"{scope} free-tier quota used up" + (f" (limit {quota.limit})" if quota.limit else "")
    if isinstance(e, errors.APIError):
        return f"{e.code} {e.status}"
    return type(e).__name__


# ------------------------------------------------------------------ request
def build_request(
    request: LLMRequest, model: str | None = None, http_options: types.HttpOptions | None = None
) -> tuple[list[types.Content], types.GenerateContentConfig]:
    model = model or request.model
    declarations = [
        types.FunctionDeclaration(
            name=tool["name"],
            description=tool.get("description", ""),
            parameters_json_schema=inline_refs(tool["input_schema"]),
        )
        for tool in request.tools
    ]
    system = request.system
    if request.output_schema is not None:
        declarations.append(
            types.FunctionDeclaration(
                name=REPORT_TOOL,
                description="Submit the final investigation report.",
                parameters_json_schema=inline_refs(request.output_schema),
            )
        )
        system += REPORT_INSTRUCTION
    tools: dict[str, Any] = {}
    if declarations:
        calling = types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.VALIDATED)
        if request.answer_now and request.output_schema is not None:
            # Budget spent or a reminder sent: the report is the only call allowed.
            calling = types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.ANY, allowed_function_names=[REPORT_TOOL]
            )
        tools = {
            "tools": [types.Tool(function_declarations=declarations)],
            "tool_config": types.ToolConfig(function_calling_config=calling),
        }
    config = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=request.max_tokens,
        thinking_config=_thinking(model, request.effort),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        http_options=http_options,
        **tools,
    )
    return to_contents(request.messages, model), config


def _thinking(model: str, effort: str | None) -> types.ThinkingConfig:
    # Gemma thinks at a fixed depth: it rejects both thinking_level and thinking_budget.
    if effort is None or model.startswith("gemma-"):
        return types.ThinkingConfig(include_thoughts=True)
    if model.startswith("gemini-2"):
        return types.ThinkingConfig(include_thoughts=True, thinking_budget=_THINKING_BUDGET.get(effort, 8192))
    return types.ThinkingConfig(include_thoughts=True, thinking_level=_THINKING_LEVEL.get(effort))


def to_contents(messages: list[dict[str, Any]], model: str) -> list[types.Content]:
    """The conversation as `model` should see it."""
    contents: list[types.Content] = []
    names: dict[str, str] = {}  # tool_use id -> function name
    # Calls of a turn that submitted the report: the investigator answers none of
    # them (a rejected report gets a reminder), but Gemini wants a response to each.
    pending: list[types.FunctionCall] = []
    for message in messages:
        blocks = message["content"]
        if isinstance(blocks, str):
            blocks = [{"type": "text", "text": blocks}]
        if message["role"] == "assistant":
            parts = _model_parts(blocks, model)
            calls = [p.function_call for p in parts if p.function_call]
            pending = calls if any(c.name == REPORT_TOOL for c in calls) else []
            names.update({b["id"]: b["name"] for b in blocks if b.get("type") == "tool_use"})
            _append(contents, "model", parts)
            continue

        parts = [
            _function_response(
                call.id,
                call.name or "unknown",
                REPORT_NOT_ACCEPTED if call.name == REPORT_TOOL else DROPPED_CALL,
                is_error=True,
            )
            for call in pending
        ]
        pending = []
        for block in blocks:
            if block.get("type") == "tool_result":
                call_id = block["tool_use_id"]
                parts.append(
                    _function_response(
                        call_id,
                        names.get(call_id, "unknown"),
                        _text(block),
                        is_error=block.get("is_error", False),
                    )
                )
            elif block.get("type") == "text" and block.get("text"):
                parts.append(types.Part(text=block["text"]))
        _append(contents, "user", parts)
    return contents


def _append(contents: list[types.Content], role: str, parts: list[types.Part]) -> None:
    """Add a turn; an empty turn is skipped, and same-role turns merge."""
    if not parts:
        return
    if contents and contents[-1].role == role:
        contents[-1].parts = [*(contents[-1].parts or []), *parts]
    else:
        contents.append(types.Content(role=role, parts=parts))


def _model_parts(blocks: list[dict[str, Any]], model: str) -> list[types.Part]:
    raw = next((b for b in blocks if b.get("type") == RAW_PARTS), None)
    if raw is not None:
        parts = [types.Part.model_validate(p) for p in raw["parts"]]
        if raw.get("model") in (None, model):
            return parts  # its own words, signatures intact
        # Another model's turn: its thoughts and signatures mean nothing here.
        return [_foreign(p) for p in parts if not p.thought]
    # History from another provider (or written by hand): rebuilt.
    parts = []
    for block in blocks:
        if block.get("type") == "text" and block.get("text"):
            parts.append(types.Part(text=block["text"]))
        elif block.get("type") == "tool_use":
            call_id = None if block["id"].startswith(_LOCAL_ID) else block["id"]
            call = types.FunctionCall(id=call_id, name=block["name"], args=block.get("input") or {})
            parts.append(types.Part(function_call=call, thought_signature=FOREIGN_SIGNATURE))
    return parts


def _foreign(part: types.Part) -> types.Part:
    return part.model_copy(update={"thought_signature": FOREIGN_SIGNATURE if part.function_call else None})


def _function_response(call_id: str | None, name: str, text: str, *, is_error: bool) -> types.Part:
    return types.Part(
        function_response=types.FunctionResponse(
            # Ids we made up for id-less calls stay on our side.
            id=None if not call_id or call_id.startswith(_LOCAL_ID) else call_id,
            name=name,
            response={"error": text} if is_error else {"output": text},
        )
    )


def _text(block: dict[str, Any]) -> str:
    content = block.get("content", "")
    if isinstance(content, str):
        return content
    return "".join(c.get("text", "") for c in content if c.get("type") == "text")


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve local ``$ref``s into a self-contained schema (no ``$defs``)."""
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                target = resolve(defs[ref.removeprefix("#/$defs/")])
                return {**target, **{k: resolve(v) for k, v in node.items() if k != "$ref"}}
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


# ----------------------------------------------------------------- response
def parse_response(
    response: types.GenerateContentResponse, model: str
) -> tuple[list[dict[str, Any]], str, str | None]:
    """Content blocks, stop reason and refusal category of one response from `model`."""
    feedback = response.prompt_feedback
    if feedback is not None and feedback.block_reason is not None:  # the prompt itself was blocked
        return [], "refusal", feedback.block_reason.value.lower()
    candidate = response.candidates[0] if response.candidates else None
    finish = candidate.finish_reason if candidate else None
    if candidate is None or candidate.content is None:
        if finish is not None and finish not in _STOP_REASONS:
            return [], "refusal", finish.value.lower()
        return [], _STOP_REASONS.get(finish, "end_turn") if finish else "end_turn", None

    parts = candidate.content.parts or []
    blocks: list[dict[str, Any]] = []
    report: dict[str, Any] | None = None
    for part in parts:
        if part.thought:
            if part.text:
                blocks.append({"type": "thinking", "thinking": part.text})
        elif part.function_call:
            call = part.function_call
            if call.name == REPORT_TOOL:
                report = dict(call.args or {})
            else:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.id or f"{_LOCAL_ID}{uuid.uuid4().hex[:12]}",
                        "name": call.name,
                        "input": dict(call.args or {}),
                    }
                )
        elif part.text:
            blocks.append({"type": "text", "text": part.text})

    refusal = None
    if report is not None:
        # The model is answering: tool calls made alongside the report are dropped.
        blocks = [b for b in blocks if b["type"] != "tool_use"]
        blocks.append({"type": "text", "text": json.dumps(report)})
        stop_reason = "end_turn"
    elif any(b["type"] == "tool_use" for b in blocks):
        stop_reason = "tool_use"
    elif finish is None or finish in _STOP_REASONS:
        stop_reason = _STOP_REASONS.get(finish, "end_turn") if finish else "end_turn"
    else:
        stop_reason, refusal = "refusal", finish.value.lower()
    blocks.append(
        {
            "type": RAW_PARTS,
            "model": model,
            "parts": [p.model_dump(mode="json", exclude_none=True) for p in parts],
        }
    )
    return blocks, stop_reason, refusal


def _usage(meta: types.GenerateContentResponseUsageMetadata | None) -> Usage:
    if meta is None:
        return Usage()
    cached = meta.cached_content_token_count or 0
    return Usage(
        input_tokens=(meta.prompt_token_count or 0) - cached + (meta.tool_use_prompt_token_count or 0),
        cache_read_tokens=cached,
        output_tokens=(meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0),
    )
