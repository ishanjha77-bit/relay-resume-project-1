"""Traces of Relay's own work: one trace per run, a span per model call and per tool call.

Spans follow OpenTelemetry's GenAI semantic conventions (`chat <model>` with
`gen_ai.*` token counts, `execute_tool <tool>`), so any OTel backend can show
where an investigation spent its time and tokens. They go over OTLP to the
cluster's OpenTelemetry Collector, which already ships the sandbox's traces to
Jaeger. Without OTEL_EXPORTER_OTLP_ENDPOINT, tracing is a no-op.
"""

from __future__ import annotations

import os
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from relay_agent.llm.types import LLMProvider, LLMRequest, LLMResponse
from relay_agent.tools.toolbox import Toolbox, ToolOutcome, ToolSpec

tracer = trace.get_tracer("relay.agent")


def configure(service_name: str = "relay-agent-service") -> bool:
    """Export spans when an OTLP endpoint is configured. Returns whether it is."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create(
        {"service.name": os.environ.get("OTEL_SERVICE_NAME", service_name), "service.namespace": "relay"}
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    return True


def _system(model: str) -> str:
    return "anthropic" if model.startswith("claude-") else "gcp.gemini"


class TracedProvider:
    """An LLM provider whose every call is a `chat <model>` span with token usage."""

    def __init__(self, inner: LLMProvider):
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        with tracer.start_as_current_span(
            f"chat {request.model}",
            kind=trace.SpanKind.CLIENT,
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.system": _system(request.model),
                "gen_ai.request.model": request.model,
                "gen_ai.request.max_tokens": request.max_tokens,
                "relay.agent.role": request.role,
                "relay.request.tools": len(request.tools),
            },
        ) as span:
            try:
                response = await self._inner.complete(request)
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, type(e).__name__))
                raise
            span.set_attributes(
                {
                    "gen_ai.response.model": response.model,
                    "gen_ai.response.finish_reasons": [response.stop_reason],
                    "gen_ai.usage.input_tokens": response.usage.prompt_tokens,
                    "gen_ai.usage.output_tokens": response.usage.output_tokens,
                    "relay.usage.cache_read_tokens": response.usage.cache_read_tokens,
                    "relay.cost_usd": response.cost_usd,
                }
            )
            return response


class TracedToolbox:
    """A toolbox whose every call is an `execute_tool <tool>` span."""

    def __init__(self, inner: Toolbox):
        self._inner = inner

    @property
    def specs(self) -> list[ToolSpec]:
        return self._inner.specs

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        server = next((s.server for s in self._inner.specs if s.name == name), "")
        with tracer.start_as_current_span(
            f"execute_tool {name}",
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": name,
                "relay.mcp.server": server,
            },
        ) as span:
            outcome = await self._inner.call(name, arguments)
            span.set_attributes(
                {"relay.tool.chars": len(outcome.text), "relay.tool.latency_ms": outcome.latency_ms}
            )
            if outcome.is_error:
                span.set_status(Status(StatusCode.ERROR, "tool error"))
            return outcome
