"""Relay's own traces: a span per model call and per tool call, with GenAI attributes."""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from relay_agent.llm.replay import ReplayProvider
from relay_agent.llm.types import LLMRequest
from relay_agent.tracing import TracedProvider, TracedToolbox
from support import LOGS, FakeToolbox, answer_turn, report

pytestmark = pytest.mark.anyio

EXPORTER = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
trace.set_tracer_provider(_provider)


async def test_model_and_tool_calls_become_genai_spans() -> None:
    EXPORTER.clear()
    provider = TracedProvider(ReplayProvider([answer_turn(report())]))
    request = LLMRequest(
        role="investigator", model="gemini-3.6-flash", system="s", messages=[], max_tokens=1000
    )
    await provider.complete(request)
    toolbox = TracedToolbox(FakeToolbox({"logs__error_summary": LOGS}))
    await toolbox.call("logs__error_summary", {"minutes": 15})

    chat, tool = EXPORTER.get_finished_spans()
    assert chat.name == "chat gemini-3.6-flash"
    assert chat.attributes["gen_ai.system"] == "gcp.gemini"
    assert chat.attributes["gen_ai.usage.output_tokens"] == 600
    assert chat.attributes["relay.agent.role"] == "investigator"
    assert tool.name == "execute_tool logs__error_summary"
    assert (tool.attributes["relay.mcp.server"], tool.attributes["relay.tool.chars"]) == ("logs", len(LOGS))
