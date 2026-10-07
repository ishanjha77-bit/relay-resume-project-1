"""Prometheus metrics: run outcomes, run time and cost per incident, and the
LLM and tool calls behind them."""

from __future__ import annotations

from typing import Any

from prometheus_client import Counter, Gauge, Histogram

RUNS = Counter("relay_agent_runs", "Finished investigations, by final status.", ["status"])
RUN_SECONDS = Histogram(
    "relay_agent_run_duration_seconds",
    "Wall time of one investigation, from pickup to verdict.",
    buckets=(15, 30, 60, 90, 120, 180, 240, 300, 450, 600),
)
RUN_COST = Histogram(
    "relay_agent_run_cost_usd",
    "LLM cost of one investigation.",
    buckets=(0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0),
)
RUNS_IN_FLIGHT = Gauge("relay_agent_runs_in_flight", "Investigations running now.")
MESSAGES = Counter(
    "relay_agent_stream_messages",
    "incident.opened messages, by how they were handled.",
    ["outcome"],
)

LLM_CALLS = Counter("relay_agent_llm_calls", "Model calls.", ["model", "stop_reason"])
LLM_TOKENS = Counter(
    "relay_agent_llm_tokens",
    "Tokens by kind: prompt (including cached), cache_read, output.",
    ["model", "kind"],
)
LLM_COST = Counter("relay_agent_llm_cost_usd", "Model spend.", ["model"])
LLM_SECONDS = Histogram(
    "relay_agent_llm_latency_seconds",
    "Latency of one model call.",
    ["model"],
    buckets=(1, 2, 5, 10, 20, 30, 60, 120),
)
TOOL_CALLS = Counter("relay_agent_tool_calls", "Tool calls, by outcome.", ["tool", "outcome"])
TOOL_SECONDS = Histogram(
    "relay_agent_tool_latency_seconds",
    "Latency of one tool call.",
    ["tool"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
INJECTION_MARKERS = Counter(
    "relay_agent_injection_markers",
    "Tool outputs that carried prompt-injection markers.",
    ["tool"],
)


class MetricsSink:
    """Turns agent steps into the per-call metrics above."""

    async def emit(self, kind: str, /, **data: Any) -> None:
        if kind == "llm.completed":
            model = data["model"]
            LLM_CALLS.labels(model, data["stop_reason"]).inc()
            LLM_TOKENS.labels(model, "prompt").inc(data["prompt_tokens"])
            LLM_TOKENS.labels(model, "cache_read").inc(data["cache_read_tokens"])
            LLM_TOKENS.labels(model, "output").inc(data["output_tokens"])
            LLM_COST.labels(model).inc(data["cost_usd"])
            LLM_SECONDS.labels(model).observe(data["latency_ms"] / 1000)
        elif kind == "tool.called":
            tool = data["tool"]
            TOOL_CALLS.labels(tool, "error" if data["is_error"] else "ok").inc()
            TOOL_SECONDS.labels(tool).observe(data["latency_ms"] / 1000)
            if data["injection_markers"]:
                INJECTION_MARKERS.labels(tool).inc()
