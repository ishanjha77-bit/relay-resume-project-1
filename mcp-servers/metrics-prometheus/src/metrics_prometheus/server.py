"""MCP server: read-only metrics, alerts and service topology from Prometheus."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from relay_mcp_kit import KitSettings, create_server, serve, to_json

from metrics_prometheus.analysis import rounded, summarize_series
from metrics_prometheus.health import snapshot
from metrics_prometheus.prom import PrometheusClient, PrometheusError, TimeWindow, parse_time, to_float

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False, idempotent_hint=True)
MAX_RESULT_CHARS = 12_000

INSTRUCTIONS = """\
Read-only access to the metrics of the system under investigation (Prometheus).
service_health gives a per-service snapshot (traffic, errors, latency,
dependencies, saturation, restarts, versions) with a baseline; use
service_dependencies to see who calls whom, and query/query_range to test a
specific hypothesis with PromQL."""


def _fit(payload: dict[str, Any], list_key: str) -> str:
    items = payload[list_key]
    text = to_json(payload)
    while len(text) > MAX_RESULT_CHARS and items:
        items.pop()
        payload["truncated"] = True
        text = to_json(payload)
    return text


def build_server(
    prom: PrometheusClient,
    namespace: str,
    settings: KitSettings | None = None,
    ignore_services: frozenset[str] = frozenset(),
) -> MCPServer:
    mcp = create_server(
        "metrics-prometheus", instructions=INSTRUCTIONS, scopes=["metrics:read"], settings=settings
    )

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def service_health(service: str | None = None, minutes: int = 30, end: str | None = None) -> str:
        """Health snapshot of every service (or one): request rate, 5xx error ratio,
        p95 latency, calls to each dependency (rate, p95, error ratio), DB connection
        pool, JVM heap/GC/blocked threads, Tomcat threads, Go goroutines, container
        CPU/throttling/memory/restarts and the versions running — each `now` versus
        `before` (the start of the window) — plus a `notable` list of large deviations.

        Args:
            service: Limit to one service (e.g. "orders"); omit for all.
            minutes: How far back the `before` baseline is taken (default 30).
            end: ISO-8601 time to evaluate `now` at; default now.
        """
        try:
            window = TimeWindow.ending(end, minutes)
            return to_json(await snapshot(prom, namespace, service, window, ignore_services))
        except (PrometheusError, ValueError) as e:
            return to_json({"error": str(e)})

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def query(promql: str, at: str | None = None) -> str:
        """Evaluate an instant PromQL query. Returns up to 30 series, largest value first.

        Conventions: HTTP RED metrics are http_server_requests_seconds_{count,sum,bucket}
        with labels service, uri, method, status, outcome (SUCCESS, CLIENT_ERROR,
        SERVER_ERROR); calls to dependencies are http_client_requests_seconds_* with
        client_name; every series has namespace and service labels.

        Args:
            promql: e.g. sum by (service) (rate(http_server_requests_seconds_count{namespace="sandbox"}[2m]))
            at: ISO-8601 evaluation time; default now.
        """
        try:
            result = await prom.instant(promql, parse_time(at) if at else None)
        except (PrometheusError, ValueError) as e:
            return to_json({"error": str(e)})
        rows = []
        for series in result:
            labels = {k: v for k, v in series["metric"].items() if k not in ("job", "instance", "cluster")}
            rows.append({"labels": labels, "value": rounded(to_float(series["value"][1]))})
        rows.sort(key=lambda r: -(r["value"] or 0))
        payload = {"series_count": len(rows), "series": rows[:30]}
        return _fit(payload, "series")

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def query_range(
        promql: str, minutes: int = 30, step_seconds: int | None = None, end: str | None = None
    ) -> str:
        """Evaluate PromQL over time and summarize each series: first/last/min/max/mean,
        the moment its level shifted (`change.at` with before/after means) and ~20
        downsampled points. Use it to find *when* something started.

        Args:
            promql: Query returning at most a handful of series (aggregate with sum by (...)).
            minutes: Range ending at `end` (default 30).
            step_seconds: Resolution; default picks ~120 points.
            end: ISO-8601 end of the range; default now.
        """
        try:
            window = TimeWindow.ending(end, minutes)
            step = step_seconds or max(15, window.minutes * 60 // 120)
            result = await prom.range(promql, window, step)
        except (PrometheusError, ValueError) as e:
            return to_json({"error": str(e)})
        payload = {
            "window": window.describe(),
            "step_seconds": step,
            "series_count": len(result),
            "series": [summarize_series(s) for s in result[:10]],
        }
        return _fit(payload, "series")

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def active_alerts() -> str:
        """Alerts currently firing or pending in Prometheus, with labels, summary and
        the time each became active."""
        try:
            alerts = await prom.alerts()
        except PrometheusError as e:
            return to_json({"error": str(e)})
        rows = [
            {
                "alert": a["labels"].get("alertname"),
                "state": a.get("state"),
                "service": a["labels"].get("service"),
                "severity": a["labels"].get("severity"),
                "active_since": a.get("activeAt"),
                "summary": (a.get("annotations") or {}).get("summary"),
            }
            for a in alerts
        ]
        rows.sort(key=lambda r: (r["state"] != "firing", r["active_since"] or ""))
        return to_json({"alerts": rows})

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def service_dependencies(minutes: int = 10, end: str | None = None) -> str:
        """Who calls whom, derived from distributed traces: one edge per caller ->
        callee with request rate, failed-request ratio and p95 latency. External
        dependencies (e.g. the payment provider "psp") and databases appear as nodes
        too. Use it to tell a failing service apart from a victim of its dependency.

        Args:
            minutes: Rate window ending at `end` (default 10).
            end: ISO-8601 evaluation time; default now.
        """
        try:
            window = TimeWindow.ending(end, minutes)
            w = f"{window.minutes}m"
            total, failed, server_p95, client_p95 = await asyncio.gather(
                prom.instant(
                    f"sum by (client, server) (rate(traces_service_graph_request_total[{w}]))", window.end
                ),
                prom.instant(
                    f"sum by (client, server) (rate(traces_service_graph_request_failed_total[{w}]))",
                    window.end,
                ),
                prom.instant(
                    "histogram_quantile(0.95, sum by (client, server, le) "
                    f"(rate(traces_service_graph_request_server_seconds_bucket[{w}])))",
                    window.end,
                ),
                prom.instant(
                    "histogram_quantile(0.95, sum by (client, server, le) "
                    f"(rate(traces_service_graph_request_client_seconds_bucket[{w}])))",
                    window.end,
                ),
            )
        except (PrometheusError, ValueError) as e:
            return to_json({"error": str(e)})

        def by_edge(result: list[dict]) -> dict[tuple[str, str], float | None]:
            return {
                (s["metric"].get("client"), s["metric"].get("server")): to_float(s["value"][1])
                for s in result
            }

        failures, p95_server, p95_client = by_edge(failed), by_edge(server_p95), by_edge(client_p95)
        edges = []
        for (client, server), rate in sorted(by_edge(total).items(), key=lambda kv: -(kv[1] or 0)):
            if client == server or not rate:
                continue
            p95 = p95_server.get((client, server)) or p95_client.get((client, server))
            edges.append(
                {
                    "from": client,
                    "to": server,
                    "rps": rounded(rate),
                    "failed_ratio": rounded((failures.get((client, server)) or 0) / rate),
                    "p95_s": rounded(p95),
                }
            )
        return to_json({"window": window.describe(), "edges": edges})

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def find_metrics(contains: str, limit: int = 40) -> str:
        """Metric names containing a substring (case-insensitive), e.g. "hikari", "gc",
        "redis", "pg_". Use before writing PromQL for an unfamiliar signal.

        Args:
            contains: Substring to look for.
            limit: Maximum names (default 40).
        """
        try:
            names = await prom.metric_names()
        except PrometheusError as e:
            return to_json({"error": str(e)})
        needle = contains.lower()
        matches = [n for n in names if needle in n.lower()]
        return to_json({"matches": matches[: max(1, min(limit, 200))], "total": len(matches)})

    return mcp


def main() -> None:
    settings = KitSettings.from_env()
    prom = PrometheusClient(
        os.environ.get("PROMETHEUS_URL", "http://prometheus.observability.svc.cluster.local:9090")
    )
    ignore = frozenset(s for s in os.environ.get("IGNORE_SERVICES", "loadgen,exporter").split(",") if s)
    mcp = build_server(prom, os.environ.get("SANDBOX_NAMESPACE", "sandbox"), settings, ignore)
    serve(mcp, settings)


if __name__ == "__main__":
    main()
