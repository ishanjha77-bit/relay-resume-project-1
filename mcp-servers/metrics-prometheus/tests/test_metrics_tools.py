"""Tool tests against a synthetic Prometheus that answers from a rule table."""

import json
from collections.abc import Callable
from datetime import UTC, datetime

import anyio
import httpx2
from metrics_prometheus.prom import PrometheusClient
from metrics_prometheus.server import build_server
from relay_mcp_kit import KitSettings

END = "2026-10-04T06:30:00Z"
END_TS = datetime(2026, 10, 4, 6, 30, tzinfo=UTC).timestamp()

# (substring of the query, phase or "*", [(labels, value), ...])
Rule = tuple[str, str, list[tuple[dict[str, str], str]]]

DB_POOL_INCIDENT: list[Rule] = [
    ('outcome="SERVER_ERROR"', "now", [({"service": "orders"}, "0.62")]),
    ('outcome="SERVER_ERROR"', "before", [({"service": "orders"}, "0")]),
    ("hikaricp_connections_active", "*", [({"service": "orders"}, "10")]),
    ("hikaricp_connections_max", "*", [({"service": "orders"}, "10")]),
    ("hikaricp_connections_pending", "now", [({"service": "orders"}, "7")]),
    ("count by (service, version)", "now", [({"service": "orders", "version": "1.4.0"}, "1")]),
    ("count by (service, version)", "before", [({"service": "orders", "version": "1.3.0"}, "1")]),
    (
        "rate(http_server_requests_seconds_count",
        "*",
        [({"service": "orders"}, "4.2"), ({"service": "loadgen"}, "1")],
    ),
]


def vector(results: list[tuple[dict[str, str], str]], ts: float) -> dict:
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [{"metric": labels, "value": [ts, value]} for labels, value in results],
        },
    }


def synthetic(rules: list[Rule], seen: list[str]) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/v1/alerts":
            return httpx2.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "alerts": [
                            {
                                "labels": {
                                    "alertname": "HighErrorRate",
                                    "service": "orders",
                                    "severity": "critical",
                                },
                                "annotations": {"summary": "orders: 62% of requests are failing"},
                                "state": "firing",
                                "activeAt": "2026-10-04T06:21:00Z",
                                "value": "0.62",
                            }
                        ]
                    },
                },
            )
        query = request.url.params["query"]
        seen.append(query)
        at = float(request.url.params.get("time", END_TS))
        phase = "now" if abs(at - END_TS) < 1 else "before"
        for needle, when, results in rules:
            if needle in query and when in ("*", phase):
                return httpx2.Response(200, json=vector(results, at))
        return httpx2.Response(200, json=vector([], at))

    return handler


def call(handler: Callable[[httpx2.Request], httpx2.Response], tool: str, args: dict) -> dict:
    http = httpx2.AsyncClient(base_url="http://prom", transport=httpx2.MockTransport(handler))
    mcp = build_server(
        PrometheusClient("http://prom", http=http),
        "sandbox",
        KitSettings(token="test"),
        ignore_services=frozenset({"loadgen"}),
    )

    async def run() -> dict:
        result = await mcp.call_tool(tool, args)
        return json.loads(result.content[0].text)

    return anyio.run(run)


def test_service_health_flags_a_saturated_pool_after_a_version_change() -> None:
    seen: list[str] = []
    out = call(synthetic(DB_POOL_INCIDENT, seen), "service_health", {"minutes": 30, "end": END})

    orders = out["services"]["orders"]
    assert orders["db_pool"]["waiting"]["now"] == 7
    notes = " | ".join(orders["notable"])
    assert "DB pool saturated: 10/10 active, 7 waiting" in notes
    assert "error ratio 62%" in notes
    assert "version changed: 1.3.0 -> 1.4.0" in notes
    assert "loadgen" not in out["services"]
    # Every canned query was scoped to the sandbox namespace.
    assert all('namespace="sandbox"' in q for q in seen)


def test_service_health_scopes_queries_to_one_service() -> None:
    seen: list[str] = []
    call(synthetic([], seen), "service_health", {"service": "payments", "end": END})
    assert any('service="payments"' in q for q in seen)
    assert any('container="payments"' in q for q in seen)


def test_active_alerts_lists_firing_first() -> None:
    out = call(synthetic([], []), "active_alerts", {})
    assert out["alerts"][0]["alert"] == "HighErrorRate"
    assert out["alerts"][0]["state"] == "firing"


def test_service_dependencies_builds_edges_without_self_loops() -> None:
    rules: list[Rule] = [
        (
            "traces_service_graph_request_total",
            "*",
            [
                ({"client": "orders", "server": "payments"}, "3.5"),
                ({"client": "orders", "server": "orders"}, "7.6"),
                ({"client": "payments", "server": "psp"}, "3.4"),
            ],
        ),
        (
            "traces_service_graph_request_failed_total",
            "*",
            [({"client": "payments", "server": "psp"}, "1.7")],
        ),
        ("request_server_seconds_bucket", "*", [({"client": "orders", "server": "payments"}, "2.9")]),
        ("request_client_seconds_bucket", "*", [({"client": "payments", "server": "psp"}, "2.8")]),
    ]
    out = call(synthetic(rules, []), "service_dependencies", {"end": END})
    edges = {(e["from"], e["to"]): e for e in out["edges"]}
    assert ("orders", "orders") not in edges
    assert edges[("payments", "psp")]["failed_ratio"] == 0.5
    assert edges[("payments", "psp")]["p95_s"] == 2.8
    assert edges[("orders", "payments")]["p95_s"] == 2.9


def test_prometheus_errors_are_reported_not_raised() -> None:
    def failing(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json={"status": "error", "error": "parse error: unexpected }"})

    out = call(failing, "query", {"promql": "sum(}"})
    assert out["error"] == "Prometheus returned 400: parse error: unexpected }"
