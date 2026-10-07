"""Command line: investigate the incident that is firing right now.

relay-agent investigate                    # alerts from Alertmanager, live tools, the configured model
relay-agent investigate --record runs/x    # ...and save a replayable recording
relay-agent investigate --replay runs/x    # re-run a recording: no network, no cost
relay-agent llm-check                      # is the configured model reachable with this key?
relay-agent serve                          # investigate every incident platform-api opens
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2

from relay_agent.config import ModelRoute, Settings
from relay_agent.events import ConsoleSink, EventSink, FanOut
from relay_agent.graph.investigator import InvestigationState, Investigator
from relay_agent.graph.schemas import Alert, Incident
from relay_agent.llm.factory import MissingCredentials, make_provider
from relay_agent.llm.replay import ReplayProvider
from relay_agent.llm.types import LLMRequest
from relay_agent.recording import Recording
from relay_agent.tools.mcp_toolbox import McpToolbox
from relay_agent.tools.replay import ReplayToolbox


async def incident_from_alertmanager(url: str, namespace: str) -> Incident | None:
    async with httpx2.AsyncClient(base_url=url, timeout=10) as http:
        response = await http.get(
            "/api/v2/alerts", params={"active": "true", "silenced": "false", "inhibited": "false"}
        )
        response.raise_for_status()
    raw = [a for a in response.json() if a["labels"].get("namespace") == namespace]
    if not raw:
        return None
    alerts = sorted(
        (
            Alert(
                name=a["labels"].get("alertname", "unknown"),
                service=a["labels"].get("service"),
                severity=a["labels"].get("severity"),
                state=a.get("status", {}).get("state", "active"),
                since=a.get("startsAt"),
                summary=a.get("annotations", {}).get("summary"),
            )
            for a in raw
        ),
        key=lambda a: (a.severity != "critical", a.since or ""),
    )
    opened = min(a.since for a in alerts if a.since) if any(a.since for a in alerts) else _now()
    lead = alerts[0]
    title = f"{lead.name} on {lead.service or namespace}" + (
        f" (+{len(alerts) - 1} more)" if len(alerts) > 1 else ""
    )
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return Incident(id=f"INC-{stamp}", title=title, opened_at=opened, namespace=namespace, alerts=alerts)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def print_report(state: InvestigationState, elapsed_s: float, out: Any = sys.stdout) -> None:
    def p(text: str = "") -> None:
        print(text, file=out)

    usage = state.get("usage", {})
    p()
    if state.get("status") != "concluded":
        p(f"Investigation {state.get('status')}: {state.get('error')}")
    report = state.get("report")
    if report:
        checks = state.get("checks", [])
        p("=" * 78)
        for i, h in enumerate(report["hypotheses"]):
            label = "ROOT CAUSE" if i == 0 else f"alternative {i}"
            p(
                f"{label}: {h['category']} in {h['service']} ({h['component']}) — confidence {h['confidence']:.2f}"
            )
            p(f"  {h['summary']}")
            for c in h["evidence"]:
                verdict = next(
                    (
                        k["verdict"]
                        for k in checks
                        if k["hypothesis"] == i
                        and k["evidence_id"] == c["evidence_id"]
                        and k["quote"] == c["quote"]
                    ),
                    "?",
                )
                mark = {
                    "verified": "verified",
                    "quote_not_found": "QUOTE NOT FOUND",
                    "unknown_evidence": "UNKNOWN EVIDENCE",
                    "reference_not_evidence": "REFERENCE, NOT EVIDENCE",
                }.get(verdict, verdict)
                p(f'    [{c["evidence_id"]} · {mark}] "{c["quote"][:110]}"')
            p(f"  fix: {h['suggested_fix']}")
            p()
        p(f"summary: {report['summary']}")
        p(f"impact:  {report['impact']}")
        p(f"started: {report.get('started_at')} · affected: {', '.join(report['affected_services'])}")
        if report["injection_suspected"]:
            p(f"⚠ prompt injection suspected in {', '.join(report['injection_evidence_ids'])}")
        p("=" * 78)
    prompt = (
        usage.get("input_tokens", 0)
        + usage.get("cache_write_5m_tokens", 0)
        + usage.get("cache_write_1h_tokens", 0)
        + usage.get("cache_read_tokens", 0)
    )
    p(
        f"cost ${state.get('cost_usd', 0):.4f} · {state.get('tool_calls', 0)} tool calls · "
        f"{state.get('llm_calls', 0)} LLM calls · {prompt} prompt tokens "
        f"({usage.get('cache_read_tokens', 0)} from cache) · {usage.get('output_tokens', 0)} output tokens · "
        f"{elapsed_s:.0f} s"
    )


def route_from(base: ModelRoute, args: argparse.Namespace) -> ModelRoute:
    """The configured route with command-line overrides; a new model re-infers its provider."""
    overrides = {
        k: v for k, v in {"model": args.model, "provider": args.provider, "effort": args.effort}.items() if v
    }
    if not overrides:
        return base
    fields = base.model_dump()
    if "model" in overrides and "provider" not in overrides:
        fields.pop("provider")
    return ModelRoute.model_validate({**fields, **overrides})


async def investigate(args: argparse.Namespace) -> int:
    settings = Settings()
    route = route_from(settings.investigator, args)
    budget = settings.budget.model_copy(update={"tool_calls": args.budget} if args.budget else {})
    sink: EventSink = ConsoleSink()

    if args.replay:
        run_dir = Path(args.replay)
        incident = Incident.model_validate_json((run_dir / "incident.json").read_text(encoding="utf-8"))
        investigator = Investigator(
            ReplayProvider.from_file(run_dir / "llm.jsonl"),
            ReplayToolbox.from_file(run_dir / "tools.jsonl"),
            route,
            budget,
            sink,
        )
        started = time.monotonic()
        state = await investigator.run(incident)
        print_report(state, time.monotonic() - started)
        return 0 if state.get("status") == "concluded" else 2

    incident = await incident_from_alertmanager(settings.alertmanager_url, settings.sandbox_namespace)
    if incident is None:
        print(
            f"No active alerts for namespace {settings.sandbox_namespace!r} in Alertmanager "
            f"({settings.alertmanager_url}). Inject a fault first: make chaos scenario=<name>"
        )
        return 1
    try:
        provider = make_provider(route, settings)
    except MissingCredentials as e:
        print(e)
        return 1

    recording = Recording(Path(args.record), incident) if args.record else None
    if recording:
        sink = FanOut(sink, recording.events)
    started = time.monotonic()
    try:
        async with McpToolbox(settings.mcp_servers) as mcp:
            llm = recording.provider(provider) if recording else provider
            toolbox = recording.toolbox(mcp) if recording else mcp
            state = await Investigator(llm, toolbox, route, budget, sink).run(incident)
    finally:
        await provider.aclose()
    elapsed = time.monotonic() - started
    print_report(state, elapsed)
    if recording:
        recording.finish(state, elapsed)
        run_dir = recording.dir
        if state.get("status") == "concluded":
            print(f"recording saved to {run_dir}")
        else:
            print(
                f"recording saved to {run_dir} for debugging; the run did not conclude, "
                "so golden-trace tests will skip it"
            )
    return 0 if state.get("status") == "concluded" else 2


async def check_llm(args: argparse.Namespace) -> int:
    """One tiny request through the configured route: is the key valid and the model reachable?"""
    settings = Settings()
    route = route_from(settings.investigator, args)
    try:
        provider = make_provider(route, settings)
    except MissingCredentials as e:
        print(f"✖ {e}")
        return 1
    request = LLMRequest(
        role="check",
        model=route.model,
        system="You are a connectivity check.",
        messages=[{"role": "user", "content": "Reply with the single word OK."}],
        effort="low",
        max_tokens=2048,
        fallbacks=route.fallbacks,
    )
    try:
        response = await provider.complete(request)
    except Exception as e:  # the API's own message says what is wrong: key, quota, billing
        print(f"✖ {route.provider} · {route.model}: {type(e).__name__}: {e}")
        return 1
    finally:
        await provider.aclose()
    print(
        f"✔ {route.provider} · {response.model} replied {response.text().strip()[:40]!r} in "
        f"{response.latency_ms} ms ({response.usage.prompt_tokens} tokens in, "
        f"{response.usage.output_tokens} out, ${response.cost_usd:.4f})"
    )
    return 0


def _route_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", help="override the investigator model, e.g. claude-opus-5-5")
    parser.add_argument("--provider", choices=["gemini", "anthropic"], help="default: follows from the model")
    parser.add_argument("--effort", choices=["minimal", "low", "medium", "high", "xhigh", "max"])


def main() -> None:
    parser = argparse.ArgumentParser(prog="relay-agent", description="Relay agent service")
    sub = parser.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("investigate", help="investigate the alerts firing now")
    _route_options(inv)
    inv.add_argument("--budget", type=int, help="maximum tool calls")
    inv.add_argument("--record", metavar="DIR", help="save a replayable recording of the run")
    inv.add_argument(
        "--replay", metavar="DIR", help="replay a recording instead of calling the model and the tools"
    )
    _route_options(sub.add_parser("llm-check", help="check that the configured model answers"))
    srv = sub.add_parser("serve", help="consume incidents from Redis and investigate them")
    srv.add_argument("--host", default="0.0.0.0")
    srv.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    if args.command == "serve":
        serve(args.host, args.port)
        return
    if args.command == "llm-check":
        sys.exit(asyncio.run(check_llm(args)))
    sys.exit(asyncio.run(investigate(args)))


def serve(host: str, port: int) -> None:
    import uvicorn

    from relay_agent.service.app import create_app
    from relay_agent.service.logs import configure_logging

    settings = Settings()
    configure_logging("agent-service", settings.service.log_level)
    from relay_agent.tracing import configure

    configure()  # OTLP traces when OTEL_EXPORTER_OTLP_ENDPOINT is set
    uvicorn.run(create_app(settings), host=host, port=port, log_config=None, access_log=False)
