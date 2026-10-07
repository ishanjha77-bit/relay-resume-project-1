"""A per-service health snapshot from ~25 canned PromQL queries.

One tool call replaces the dozen queries an engineer types at the start of an
incident, so the agent spends its step budget on hypotheses instead of on
rediscovering metric names. Each signal is evaluated twice — now, and at the
start of the window — so every number comes with its own baseline.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from string import Template
from typing import Any

from metrics_prometheus.analysis import rounded
from metrics_prometheus.prom import PrometheusClient, PrometheusError, TimeWindow, to_float

_HTTP = "http_server_requests_seconds"
_CLIENT = "http_client_requests_seconds"
_REQ = '$sel,uri!~"/actuator.*"'


@dataclass(frozen=True)
class Signal:
    path: tuple[str, ...]  # where the value goes; "{label}" parts are filled from series labels
    query: str  # PromQL with $sel, $csel, $ns, $w, $lookback placeholders
    phases: tuple[str, ...] = ("now", "before")
    service: str | None = None  # fixed owner when the series has no `service` label
    label: str | None = None  # collect this label's values instead of the sample value


SIGNALS: list[Signal] = [
    # --- traffic, errors, latency (RED) ---
    Signal(("rps",), f"sum by (service) (rate({_HTTP}_count{{{_REQ}}}[$w]))"),
    Signal(
        ("error_ratio",),
        f'(sum by (service) (rate({_HTTP}_count{{{_REQ},outcome="SERVER_ERROR"}}[$w]))'
        f" or sum by (service) (rate({_HTTP}_count{{{_REQ}}}[$w])) * 0)"
        f" / sum by (service) (rate({_HTTP}_count{{{_REQ}}}[$w]))",
    ),
    Signal(
        ("p95_s",), f"histogram_quantile(0.95, sum by (service, le) (rate({_HTTP}_bucket{{{_REQ}}}[$w])))"
    ),
    # --- calls to dependencies, as seen by the caller ---
    Signal(
        ("outbound", "{client_name}", "rps"),
        f"sum by (service, client_name) (rate({_CLIENT}_count{{$sel}}[$w]))",
    ),
    Signal(
        ("outbound", "{client_name}", "p95_s"),
        f"histogram_quantile(0.95, sum by (service, client_name, le) (rate({_CLIENT}_bucket{{$sel}}[$w])))",
    ),
    Signal(
        ("outbound", "{client_name}", "error_ratio"),
        f'(sum by (service, client_name) (rate({_CLIENT}_count{{$sel,outcome=~"SERVER_ERROR|UNKNOWN"}}[$w]))'
        f" or sum by (service, client_name) (rate({_CLIENT}_count{{$sel}}[$w])) * 0)"
        f" / sum by (service, client_name) (rate({_CLIENT}_count{{$sel}}[$w]))",
    ),
    # --- saturation: DB pool, JVM, Tomcat, Go runtime ---
    Signal(("db_pool", "active"), "sum by (service) (hikaricp_connections_active{$sel})"),
    Signal(("db_pool", "max"), "max by (service) (hikaricp_connections_max{$sel})"),
    Signal(("db_pool", "waiting"), "sum by (service) (hikaricp_connections_pending{$sel})"),
    Signal(
        ("db_pool", "timeouts_per_s"), "sum by (service) (rate(hikaricp_connections_timeout_total{$sel}[$w]))"
    ),
    Signal(("db_pool", "max_hold_s"), "max by (service) (hikaricp_connections_usage_seconds_max{$sel})"),
    Signal(
        ("jvm", "heap_used_ratio"),
        'sum by (service) (jvm_memory_used_bytes{$sel,area="heap"})'
        ' / sum by (service) (jvm_memory_max_bytes{$sel,area="heap"} > 0)',
    ),
    Signal(("jvm", "gc_time_ratio"), "sum by (service) (rate(jvm_gc_pause_seconds_sum{$sel}[$w]))"),
    Signal(("jvm", "threads_blocked"), 'sum by (service) (jvm_threads_states_threads{$sel,state="blocked"})'),
    Signal(("tomcat", "busy_threads"), "sum by (service) (tomcat_threads_busy_threads{$sel})"),
    Signal(("tomcat", "max_threads"), "max by (service) (tomcat_threads_config_max_threads{$sel})"),
    Signal(("go", "goroutines"), "sum by (service) (go_goroutines{$sel})"),
    Signal(("go", "heap_inuse_bytes"), "sum by (service) (go_memstats_heap_inuse_bytes{$sel})"),
    # --- container resources and lifecycle ---
    Signal(
        ("container", "cpu_cores"), "sum by (service) (rate(container_cpu_usage_seconds_total{$sel}[$w]))"
    ),
    Signal(
        ("container", "cpu_throttled_ratio"),
        "sum by (service) (rate(container_cpu_cfs_throttled_periods_total{$sel}[$w]))"
        " / sum by (service) (rate(container_cpu_cfs_periods_total{$sel}[$w]))",
    ),
    Signal(
        ("container", "memory_used_ratio"),
        "sum by (service) (container_memory_working_set_bytes{$sel})"
        " / sum by (service) (container_spec_memory_limit_bytes{$sel} > 0)",
    ),
    Signal(
        ("container", "restarts"),
        'sum by (service) (label_replace(increase(kube_pod_container_status_restarts_total{namespace="$ns"$csel}[$lookback]),'
        ' "service", "$1", "container", "(.*)"))',
        phases=("now",),
    ),
    Signal(
        ("container", "last_termination"),
        'max by (service, reason) (label_replace(kube_pod_container_status_last_terminated_reason{namespace="$ns"$csel},'
        ' "service", "$1", "container", "(.*)")) == 1',
        phases=("now",),
        label="reason",
    ),
    Signal(("versions",), "count by (service, version) (up{$sel})", label="version"),
    # --- the shared database ---
    Signal(("up",), 'max(pg_up{namespace="$ns"})', service="postgres"),
    Signal(
        ("connections", "{state}"),
        'sum by (state) (pg_stat_activity_count{namespace="$ns"})',
        service="postgres",
    ),
    Signal(("locks", "{mode}"), 'sum by (mode) (pg_locks_count{namespace="$ns"})', service="postgres"),
    Signal(
        ("longest_transaction_s",),
        'max(pg_stat_activity_max_tx_duration{namespace="$ns"})',
        service="postgres",
    ),
]


async def snapshot(
    prom: PrometheusClient,
    namespace: str,
    service: str | None,
    window: TimeWindow,
    ignore: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    params = {
        "ns": namespace,
        "sel": f'namespace="{namespace}"' + (f',service="{service}"' if service else ""),
        "csel": f',container="{service}"' if service else "",
        "lookback": f"{max(window.minutes, 1)}m",
    }
    jobs: list[tuple[Signal, str]] = []
    calls = []
    for signal in SIGNALS:
        if service and signal.service and signal.service != service:
            continue
        for phase in signal.phases:
            at, rate_window = (window.end, "2m") if phase == "now" else (window.start, "5m")
            query = Template(signal.query).safe_substitute(params, w=rate_window)
            jobs.append((signal, phase))
            calls.append(prom.instant(query, at))
    results = await asyncio.gather(*calls, return_exceptions=True)

    services: dict[str, dict[str, Any]] = {}
    failed: list[str] = []
    for (signal, phase), result in zip(jobs, results, strict=True):
        if isinstance(result, PrometheusError):
            failed.append(f"{'.'.join(signal.path)}: {result}")
            continue
        if isinstance(result, BaseException):
            raise result
        for series in result:
            labels = series["metric"]
            owner = signal.service or labels.get("service")
            if not owner or owner in ignore:
                continue
            path = [
                labels.get(part[1:-1], "unknown") if part.startswith("{") else part for part in signal.path
            ]
            node = services.setdefault(owner, {})
            for key in path[:-1]:
                node = node.setdefault(key, {})
            leaf = node.setdefault(path[-1], {})
            if signal.label:
                if value := labels.get(signal.label):
                    leaf.setdefault(phase, []).append(value)
            else:
                leaf[phase] = rounded(to_float(series["value"][1]))

    for name, data in services.items():
        data["notable"] = notable(data)
        if name == "postgres":
            data["notable"] += _postgres_notes(data)
    out: dict[str, Any] = {"window": window.describe(), "services": dict(sorted(services.items()))}
    if failed:
        out["failed_signals"] = failed
    return out


def _v(data: dict[str, Any], *path: str, phase: str = "now") -> Any:
    node: Any = data
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node.get(phase) if isinstance(node, dict) else None


def _pct(x: float) -> str:
    return f"{x:.0%}"


def notable(d: dict[str, Any]) -> list[str]:
    """Plain-language flags for values far from normal; the raw numbers stay alongside."""
    notes: list[str] = []
    er, er0 = _v(d, "error_ratio"), _v(d, "error_ratio", phase="before") or 0.0
    if er is not None and er > 0.05 and er > 3 * er0:
        notes.append(f"error ratio {_pct(er)} (was {_pct(er0)})")
    p95, p950 = _v(d, "p95_s"), _v(d, "p95_s", phase="before")
    if p95 is not None and p95 > 1 and (p950 is None or p95 > 2 * p950):
        notes.append(f"p95 latency {p95}s (was {p950}s)")
    for dep, stats in (d.get("outbound") or {}).items():
        dp, dp0 = _v(stats, "p95_s"), _v(stats, "p95_s", phase="before")
        if dp is not None and dp > 1 and (dp0 is None or dp > 2 * dp0):
            notes.append(f"calls to {dep}: p95 {dp}s (was {dp0}s)")
        de = _v(stats, "error_ratio")
        if de is not None and de > 0.05:
            notes.append(f"calls to {dep}: {_pct(de)} fail")
    active, cap, waiting = _v(d, "db_pool", "active"), _v(d, "db_pool", "max"), _v(d, "db_pool", "waiting")
    if cap and active is not None and (active >= cap or (waiting or 0) > 0):
        notes.append(f"DB pool saturated: {active:g}/{cap:g} active, {waiting or 0:g} waiting")
    if (heap := _v(d, "jvm", "heap_used_ratio")) is not None and heap > 0.85:
        notes.append(f"JVM heap {_pct(heap)} full")
    if (gc := _v(d, "jvm", "gc_time_ratio")) is not None and gc > 0.1:
        notes.append(f"{_pct(gc)} of time in GC pauses")
    busy, max_threads = _v(d, "tomcat", "busy_threads"), _v(d, "tomcat", "max_threads")
    if busy is not None and max_threads and busy >= 0.9 * max_threads:
        notes.append(f"Tomcat worker threads exhausted: {busy:g}/{max_threads:g} busy")
    if (blocked := _v(d, "jvm", "threads_blocked")) is not None and blocked >= 5:
        notes.append(f"{blocked:g} JVM threads BLOCKED")
    g, g0 = _v(d, "go", "goroutines"), _v(d, "go", "goroutines", phase="before")
    if g is not None and g0 and g > 1000 and g > 3 * g0:
        notes.append(f"goroutines {g:g} (was {g0:g})")
    if (thr := _v(d, "container", "cpu_throttled_ratio")) is not None and thr > 0.25:
        notes.append(f"CPU throttled in {_pct(thr)} of periods")
    if (mem := _v(d, "container", "memory_used_ratio")) is not None and mem > 0.9:
        notes.append(f"container memory at {_pct(mem)} of limit")
    restarts = _v(d, "container", "restarts")
    if restarts and restarts >= 1:
        reasons = _v(d, "container", "last_termination") or []
        notes.append(
            f"restarted {restarts:g} times in window (last termination: {', '.join(reasons) or 'unknown'})"
        )
    else:
        # A last-termination reason from long ago is noise; only report it with recent restarts.
        (d.get("container") or {}).pop("last_termination", None)
    now_v, before_v = _v(d, "versions"), _v(d, "versions", phase="before")
    if now_v and before_v and sorted(now_v) != sorted(before_v):
        notes.append(f"version changed: {', '.join(sorted(before_v))} -> {', '.join(sorted(now_v))}")
    elif now_v and len(now_v) > 1:
        notes.append(f"several versions running: {', '.join(sorted(now_v))}")
    return notes


def _postgres_notes(d: dict[str, Any]) -> list[str]:
    notes = []
    if _v(d, "up") == 0:
        notes.append("postgres is DOWN (pg_up == 0)")
    if (tx := _v(d, "longest_transaction_s")) is not None and tx > 2:
        notes.append(f"longest open transaction {tx:g}s")
    waiting = sum(
        v.get("now") or 0 for k, v in (d.get("connections") or {}).items() if "idle in transaction" in k
    )
    if waiting >= 5:
        notes.append(f"{waiting:g} connections idle in transaction")
    return notes
