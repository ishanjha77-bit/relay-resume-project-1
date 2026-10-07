"""Tool tests against recorded Loki responses (tests/fixtures), no network."""

import json
import pathlib
from collections.abc import Callable

import anyio
import httpx2
from logs_loki.loki import LokiClient
from logs_loki.server import build_server
from relay_mcp_kit import KitSettings

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
# The fixtures were recorded at about this time; anchoring `end` keeps bucketing deterministic.
END = "2026-10-04T06:23:00Z"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def recorded_loki(seen: list[httpx2.Request]) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if "count_over_time" in request.url.params["query"]:
            return httpx2.Response(200, json=fixture("matrix_errors_per_minute.json"))
        return httpx2.Response(200, json=fixture("streams_warn_errors.json"))

    return handler


def call(handler: Callable[[httpx2.Request], httpx2.Response], tool: str, args: dict) -> dict:
    http = httpx2.AsyncClient(base_url="http://loki", transport=httpx2.MockTransport(handler))
    mcp = build_server(LokiClient("http://loki", http=http), "sandbox", KitSettings(token="test"))

    async def run() -> dict:
        result = await mcp.call_tool(tool, args)
        return json.loads(result.content[0].text)

    return anyio.run(run)


def test_error_summary_groups_lines_into_patterns() -> None:
    seen: list[httpx2.Request] = []
    out = call(recorded_loki(seen), "error_summary", {"minutes": 10, "end": END})

    assert out["lines_examined"] > 0
    patterns = out["patterns"]
    assert patterns, "expected at least one pattern"
    assert all({"service", "level", "count", "pattern", "first_seen"} <= p.keys() for p in patterns)
    # Errors sort before warnings, larger groups first.
    levels = [p["level"] for p in patterns]
    assert levels == sorted(levels, key=lambda lvl: lvl != "ERROR")
    # Per-minute counts come from the metric query and are bucketed into the window.
    counts = out["errors_per_bucket"]["counts"]
    assert counts["orders"][4] == 85
    assert out["errors_per_bucket"]["first_error_bucket"]["orders"].startswith("2026-10-04T06:")


def test_search_logs_builds_safe_logql() -> None:
    seen: list[httpx2.Request] = []
    out = call(
        recorded_loki(seen),
        "search_logs",
        {"service": "orders", "contains": 'timed "out"', "level": "error", "end": END},
    )
    query = seen[0].url.params["query"]
    assert query == (
        '{k8s_namespace_name="sandbox", service_name="orders"} '
        '|= "timed \\"out\\"" | detected_level=~"error|fatal|critical"'
    )
    assert out["returned"] == len(out["lines"])


def test_search_logs_rejects_label_injection() -> None:
    out = call(recorded_loki([]), "search_logs", {"service": 'orders"} or {x="y'})
    assert "invalid service" in out["error"]


def test_run_logql_requires_namespace_selector() -> None:
    out = call(recorded_loki([]), "run_logql", {"query": '{service_name="orders"}'})
    assert "k8s_namespace_name" in out["error"]
    out = call(recorded_loki([]), "run_logql", {"query": 'sum(rate({k8s_namespace_name="sandbox"}[1m]))'})
    assert "error" in out


def test_loki_errors_are_reported_not_raised() -> None:
    def failing(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, text="parse error at line 1")

    out = call(failing, "search_logs", {"service": "orders"})
    assert out["error"].startswith("Loki returned 400")
