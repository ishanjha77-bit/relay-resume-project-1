"""Claude via the Anthropic SDK.

Request shape per model family (see docs/adr/0007-llm-provider-and-routing.md):

- Adaptive thinking with ``display: "updates"``: the model's notes between tool
  calls come back as short summaries the console streams as the agent's
  progress; the reasoning itself stays hidden.
- ``output_config.effort`` set explicitly: Opus 5.5 defaults to ``medium``.
- ``output_config.format`` for the final answer: the investigation report is
  schema-valid JSON without forcing a tool call (Opus 5.5 rejects forced
  ``tool_choice``).
- Prompt caching: a breakpoint on the system prompt caches tools + system
  across incidents; top-level automatic caching covers the growing conversation.
- ``prefix_mismatch_behavior: "error"``: the agent loop is append-only; if a
  change ever edits history, the API fails loudly instead of silently
  dropping the model's earlier thinking.
- ``fallbacks: "default"``: if a safety classifier declines a turn, the API
  retries it on the model Anthropic recommends for that category.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import anthropic

from relay_agent.llm.pricing import cost_usd
from relay_agent.llm.types import LLMRequest, LLMResponse, Usage

THINKING_UPDATES_BETA = "thinking-display-updates-2026-08-18"
THINKING_BINDING_BETA = "thinking-binding-controls-2026-08-01"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass(frozen=True)
class ModelProfile:
    adaptive_thinking: bool = True  # thinking always on; effort is the control
    effort: bool = True
    progress_updates: bool = True  # thinking.display = "updates"
    default_fallbacks: bool = True  # fallbacks: "default"


PROFILES: dict[str, ModelProfile] = {
    "claude-opus-5-5": ModelProfile(),
    "claude-sonnet-5-5": ModelProfile(),
    "claude-fable-5-1": ModelProfile(),
    # Haiku 4.5 predates adaptive thinking and rejects `effort`.
    "claude-haiku-4-5": ModelProfile(
        adaptive_thinking=False, effort=False, progress_updates=False, default_fallbacks=False
    ),
}


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None = None, client: anthropic.AsyncAnthropic | None = None):
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key, max_retries=3)

    async def aclose(self) -> None:
        await self._client.close()

    def build_params(self, request: LLMRequest) -> tuple[dict[str, Any], list[str]]:
        profile = PROFILES.get(request.model, ModelProfile(progress_updates=False, default_fallbacks=False))
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            # Breakpoint 1: tools + system, shared by every investigation.
            "system": [{"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}],
            "messages": request.messages,
            # Breakpoint 2 (automatic): the conversation so far, re-read on every turn.
            "cache_control": {"type": "ephemeral"},
        }
        if request.tools:
            params["tools"] = request.tools
        output_config: dict[str, Any] = {}
        if request.effort and profile.effort:
            output_config["effort"] = request.effort
        if request.output_schema:
            output_config["format"] = {"type": "json_schema", "schema": request.output_schema}
        if output_config:
            params["output_config"] = output_config

        betas: list[str] = []
        if profile.adaptive_thinking:
            thinking: dict[str, Any] = {
                "type": "adaptive",
                "block_binding": {"prefix_mismatch_behavior": "error"},
            }
            betas.append(THINKING_BINDING_BETA)
            if profile.progress_updates:
                thinking["display"] = "updates"
                betas.append(THINKING_UPDATES_BETA)
            params["thinking"] = thinking
        if profile.default_fallbacks:
            params["fallbacks"] = "default"
            betas.append(FALLBACK_BETA)
        return params, betas

    async def complete(self, request: LLMRequest) -> LLMResponse:
        params, betas = self.build_params(request)
        started = time.monotonic()
        response = await self._client.beta.messages.create(betas=betas, **params)
        latency_ms = int((time.monotonic() - started) * 1000)

        usage = _usage(response.usage)
        refusal = None
        if response.stop_reason == "refusal" and getattr(response, "stop_details", None):
            refusal = getattr(response.stop_details, "category", None) or "unspecified"
        return LLMResponse(
            content=[block.model_dump(mode="json", exclude_none=True) for block in response.content],
            stop_reason=response.stop_reason or "unknown",
            model=response.model,
            usage=usage,
            cost_usd=cost_usd(response.model, usage),
            latency_ms=latency_ms,
            request_id=getattr(response, "_request_id", None),
            refusal_category=refusal,
        )


def _usage(raw: Any) -> Usage:
    writes_5m = writes_1h = 0
    breakdown = getattr(raw, "cache_creation", None)
    if breakdown is not None:
        writes_5m = getattr(breakdown, "ephemeral_5m_input_tokens", 0) or 0
        writes_1h = getattr(breakdown, "ephemeral_1h_input_tokens", 0) or 0
    else:
        writes_5m = getattr(raw, "cache_creation_input_tokens", 0) or 0
    return Usage(
        input_tokens=raw.input_tokens or 0,
        output_tokens=raw.output_tokens or 0,
        cache_write_5m_tokens=writes_5m,
        cache_write_1h_tokens=writes_1h,
        cache_read_tokens=getattr(raw, "cache_read_input_tokens", 0) or 0,
    )
