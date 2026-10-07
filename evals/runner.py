"""Run the eval scenarios end to end against the deployed system, and score them.

For each scenario (evals/scenarios/*.yaml): wait until the sandbox is quiet,
inject the fault, and let Relay work exactly as in production (alert ->
incident -> agent). Score the incident it produces, then mitigate (reset the
sandbox), wait for the alerts to clear and resolve the incident.

Results go to evals/reports/<batch>/: one JSON per scenario, the replayable
recording of each run, and the scorecard (scorecard.md / scorecard.json).
They also go to platform-api (POST /api/evals/runs) for the console's evals
dashboard.

A batch is resumable: scenarios already scored in it are skipped. On Gemini's
free tier the daily quota usually runs out partway through a batch; the
runner then stops cleanly, and running it again after the reset (midnight
Pacific time) picks up where it left off.

  make eval label="baseline: one investigator agent"   # every scenario, today's batch
  make eval scenarios=db-pool,psp-latency batch=2026-10-05-baseline
  make eval-report batch=2026-10-05-baseline            # rebuild the scorecard only
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from scoring import aggregate, markdown, score, unanswered

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "evals" / "scenarios"
REPORTS = ROOT / "evals" / "reports"
VERDICT = ("DIAGNOSED", "AWAITING_APPROVAL", "FAILED")
FINISHED = ("run.finished", "run.failed")  # the last step of a run
QUOTA_MARKERS = ("daily quota is used up", "RESOURCE_EXHAUSTED")
# The model provider failing, after retries and every fallback model: not the agent's verdict.
PROVIDER_MARKERS = ("499 CANCELLED", "500 INTERNAL", "503 UNAVAILABLE", "504 DEADLINE_EXCEEDED")
# kubectl's answers while the cluster wakes up; a reset is idempotent, so it retries through them.
TRANSIENT = ("TLS handshake timeout", "Unable to connect to the server", "connection refused", "i/o timeout")
SUSPEND_GAP_S = 120


class QuotaSpent(Exception):
    """The model quota for today is gone: stop the batch, resume after the reset."""


class SandboxStuck(Exception):
    """The sandbox did not recover; later scenarios would be contaminated."""


class ProviderFailed(Exception):
    """The model provider failed the run (not the agent): it is not scored, and a rerun retries it."""


class Suspended(Exception):
    """The machine slept mid-scenario: its timings are void, so it is not scored."""


def log(message: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S')}  {message}", flush=True)


def now() -> datetime:
    return datetime.now(UTC)


def awake_since(polled: float, slept_s: float = 0) -> None:
    """A laptop that sleeps stops the sandbox but not the wall clock: a jump in it means a suspend."""
    if (gap := time.time() - polled - slept_s) > SUSPEND_GAP_S:
        raise Suspended(f"the machine was suspended for about {gap / 60:.0f} min")


def wait_for[T](check: Callable[[], T | None], timeout_s: float, every_s: float = 5.0) -> T | None:
    deadline = time.monotonic() + timeout_s
    while True:
        polled = time.time()
        try:
            result = check()
        except httpx.TransportError:
            awake_since(polled)
            raise
        awake_since(polled)
        if result:
            return result
        if time.monotonic() >= deadline:
            return None
        time.sleep(every_s)
        awake_since(polled, every_s)


class Relay:
    """The deployed system, as the runner sees it: platform-api, agent-service, Alertmanager."""

    def __init__(self) -> None:
        self.api = os.environ.get("RELAY_API", "http://localhost:8081")
        self.agent = os.environ.get("RELAY_AGENT_URL", "http://localhost:8000")
        self.alertmanager = os.environ.get("ALERTMANAGER_URL", "http://localhost:9093")
        self.namespace = os.environ.get("SANDBOX_NAMESPACE", "sandbox")
        self.http = httpx.Client(timeout=30)
        self.token = self._login()
        self.agent_token = os.environ.get("RELAY_AGENT_TOKEN") or _secret("agent-api-token")

    def _login(self) -> str:
        response = self.http.post(
            f"{self.api}/api/auth/token",
            json={"username": "alice", "password": os.environ.get("RELAY_DEMO_PASSWORD", "relay-demo")},
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def _get(self, path: str, **params: Any) -> Any:
        response = self.http.get(
            f"{self.api}{path}", params=params, headers={"Authorization": f"Bearer {self.token}"}
        )
        response.raise_for_status()
        return response.json()

    def active_incidents(self) -> list[dict[str, Any]]:
        return self._get("/api/incidents", status="active")["items"]

    def incident(self, incident_id: str) -> dict[str, Any]:
        return self._get(f"/api/incidents/{incident_id}")

    def steps(self, incident_id: str) -> list[dict[str, Any]]:
        return self._get(f"/api/incidents/{incident_id}/steps", limit=1000)

    def resolve(self, incident_id: str) -> None:
        self.http.post(
            # No postmortem: one written for a drill would teach later runs the answer.
            f"{self.api}/api/incidents/{incident_id}/resolve?postmortem=false",
            headers={"Authorization": f"Bearer {self.token}"},
        ).raise_for_status()

    def record(self, row: dict[str, Any]) -> bool:
        try:
            self.http.post(
                f"{self.api}/api/evals/runs", json=row, headers={"Authorization": f"Bearer {self.token}"}
            ).raise_for_status()
            return True
        except httpx.HTTPError as e:
            log(f"  could not store the result in platform-api ({e}); it is kept in evals/reports")
            return False

    def firing(self) -> list[dict[str, Any]]:
        response = self.http.get(
            f"{self.alertmanager}/api/v2/alerts",
            params={"active": "true", "silenced": "false", "inhibited": "false"},
        )
        response.raise_for_status()
        return [a for a in response.json() if a["labels"].get("namespace") == self.namespace]

    def recording(self, run_id: str) -> dict[str, Any] | None:
        if not self.agent_token:
            return None
        response = self.http.get(
            f"{self.agent}/runs/{run_id}/recording", headers={"Authorization": f"Bearer {self.agent_token}"}
        )
        return response.json() if response.status_code == 200 else None


def _secret(key: str) -> str | None:
    """A value of the relay Secret, read with kubectl (local dev)."""
    try:
        out = subprocess.run(
            [
                "kubectl",
                "--context",
                "kind-relay",
                "-n",
                "relay",
                "get",
                "secret",
                "relay-secrets",
                "-o",
                f"jsonpath={{.data.{key}}}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout
        return base64.b64decode(out).decode() or None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def sandbox(script: str, attempts: int = 1) -> None:
    bash = shutil.which("bash") or "bash"
    for attempt in range(1, attempts + 1):
        done = subprocess.run([bash, f"sandbox/chaos/{script}.sh"], cwd=ROOT, capture_output=True, text=True)
        if done.returncode == 0:
            return
        output = f"{done.stdout[-2000:]}\n{done.stderr[-2000:]}"
        if attempt == attempts or not any(marker in output for marker in TRANSIENT):
            raise RuntimeError(f"sandbox/chaos/{script}.sh failed:\n{output}")
        log(f"  {script}.sh: the Kubernetes API is not answering; retrying in 30 s")
        time.sleep(30)


def reset() -> None:
    sandbox("reset", attempts=4)


def settle(relay: Relay, quiet_s: float) -> None:
    """Resolve leftover incidents once the alerts behind them have cleared."""
    if relay.firing() and not wait_for(lambda: not relay.firing() or None, quiet_s, 10):
        names = sorted(
            {
                a["labels"].get("alertname", "?") + "/" + a["labels"].get("service", "?")
                for a in relay.firing()
            }
        )
        raise SandboxStuck(f"alerts still firing after {quiet_s:.0f} s: {', '.join(names)}")
    for incident in relay.active_incidents():
        relay.resolve(incident["id"])


def investigate(
    relay: Relay, scenario: dict[str, Any], batch_dir: Path, injected: datetime
) -> dict[str, Any]:
    timeouts = scenario.get("timeouts", {})
    alerts_s = timeouts.get("alerts_s", 360)
    investigation_s = timeouts.get("investigation_s", 600)

    incident = wait_for(lambda: next(iter(relay.active_incidents()), None), alerts_s + 60)
    if incident is None:
        return unanswered(scenario, "no_alert", f"no incident within {alerts_s + 60} s of the fault")
    incident_id = incident["id"]
    log(f"  {incident['key']} opened after {(now() - injected).total_seconds():.0f} s; the agent is on it")

    # AWAITING_APPROVAL: diagnosed, and the fixer asks to act on it.
    final = wait_for(
        lambda: (d := relay.incident(incident_id))["incident"]["status"] in VERDICT and d,
        investigation_s,
    )
    if not final:
        return unanswered(scenario, "timeout", f"no verdict within {investigation_s} s of the incident")
    if final["incident"]["status"] == "FAILED":
        error = next((e["message"] for e in final["timeline"] if e["kind"] == "agent.failed"), "")
        if any(marker in error for marker in QUOTA_MARKERS):
            raise QuotaSpent(error)
        if any(marker in error for marker in PROVIDER_MARKERS):
            raise ProviderFailed(error)
    # The verdict comes first; the reviewer and the fixer follow. Score the run as it ends.
    steps = wait_for(lambda: (s := relay.steps(incident_id)) and s[-1]["kind"] in FINISHED and s, 90, 3)
    final = relay.incident(incident_id)
    row = score(scenario, final, steps or relay.steps(incident_id), injected)
    if final.get("run_id") and (bundle := relay.recording(final["run_id"])):
        from relay_agent.recording import save

        save(bundle, batch_dir / "recordings" / scenario["id"])
    return row


def run_scenario(
    relay: Relay, scenario: dict[str, Any], batch_dir: Path, quiet_s: float, meta: dict[str, Any]
) -> dict[str, Any]:
    """Inject, score, mitigate. The row is kept before mitigating, so a failed reset can't lose it."""
    settle(relay, quiet_s)
    injected = now()
    log(f"  injecting {scenario['chaos']}")
    sandbox(scenario["chaos"])
    row: dict[str, Any] | None = None
    try:
        row = investigate(relay, scenario, batch_dir, injected) | meta
        (batch_dir / f"{scenario['id']}.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
        relay.record(row)
        return row
    finally:
        log("  mitigating: resetting the sandbox")
        reset()
        try:
            settle(relay, quiet_s)
        except SandboxStuck as e:
            if row is None:
                raise
            # The verdict is in; the next scenario waits for a quiet sandbox (or stops the batch) itself.
            log(f"  sandbox not quiet yet ({e}); keeping the verdict")


def write_scorecard(batch: str, label: str, batch_dir: Path) -> dict[str, Any]:
    rows = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(batch_dir.glob("*.json"))
        if p.stem not in ("scorecard", "batch")
    ]
    card = aggregate(rows)
    (batch_dir / "scorecard.json").write_text(
        json.dumps({"batch": batch, "label": label, **card}, indent=2), encoding="utf-8"
    )
    (batch_dir / "scorecard.md").write_text(markdown(batch, label, card, rows), encoding="utf-8")
    return card


def load_scenarios(names: list[str] | None) -> list[dict[str, Any]]:
    scenarios = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(SCENARIOS.glob("*.yaml"))]
    if names:
        known = {s["id"] for s in scenarios}
        if unknown := sorted(set(names) - known):
            raise SystemExit(f"unknown scenarios: {', '.join(unknown)} (known: {', '.join(sorted(known))})")
        scenarios = [s for s in scenarios if s["id"] in names]
    return scenarios


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--scenarios", help="comma-separated scenario ids (default: all)")
    parser.add_argument("--batch", help="batch name (default: today's date)")
    parser.add_argument("--label", default="", help="what this batch measures, for the scorecard")
    parser.add_argument("--report", action="store_true", help="only rebuild the scorecard of --batch")
    parser.add_argument("--quiet-s", type=float, default=600, help="how long to wait for alerts to clear")
    args = parser.parse_args()
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    batch = args.batch or now().strftime("%Y-%m-%d")
    batch_dir = REPORTS / batch
    batch_dir.mkdir(parents=True, exist_ok=True)
    meta = batch_dir / "batch.json"
    label = args.label or (json.loads(meta.read_text(encoding="utf-8"))["label"] if meta.exists() else "")
    meta.write_text(json.dumps({"batch": batch, "label": label}, indent=2), encoding="utf-8")
    if args.report:
        card = write_scorecard(batch, label, batch_dir)
        log(f"scorecard: {batch_dir / 'scorecard.md'} (accuracy {card['accuracy']})")
        return 0

    relay = Relay()
    if not relay.agent_token:
        log("no agent-service token (RELAY_AGENT_TOKEN or the relay Secret): runs will not be recorded")
    scenarios = load_scenarios(args.scenarios.split(",") if args.scenarios else None)
    todo = [s for s in scenarios if not (batch_dir / f"{s['id']}.json").exists()]
    log(
        f"batch {batch}: {len(scenarios) - len(todo)} of {len(scenarios)} scenarios already done, {len(todo)} to run"
    )

    status = 0
    if todo:
        log("resetting the sandbox, in case an earlier batch was interrupted")
        reset()
    for i, scenario in enumerate(todo, 1):
        log(f"[{i}/{len(todo)}] {scenario['id']}: {scenario['title']}")
        meta = {"batch": batch, "run_at": now().isoformat()}
        try:
            row = run_scenario(relay, scenario, batch_dir, args.quiet_s, meta)
        except Suspended as e:
            log(f"  stopping: {e} during {scenario['id']}, so it is not scored")
            log(f"  keep the machine awake (lid open, on power) and rerun to continue batch {batch}")
            status = 5
            break
        except ProviderFailed as e:
            log(
                f"  not scored: the model provider failed the run ({str(e)[:120]}); rerun the batch to retry it"
            )
            status = 6
            continue
        except QuotaSpent as e:
            log(f"  stopping: the free model quota is spent for today ({str(e)[:160]})")
            log(f"  run the same command after midnight Pacific time to continue batch {batch}")
            status = 3
            break
        except SandboxStuck as e:
            log(f"  stopping: {e}. Run `make reset`, check Grafana, then rerun to continue.")
            status = 4
            break
        verdict = {True: "correct", False: "wrong"}.get(row.get("correct"), row["status"])
        if row.get("top_category"):
            log(
                f"  {verdict}: {row.get('top_category')} in {row.get('top_service')} "
                f"({row.get('seconds')} s, {row.get('steps')} tool calls, {row.get('model')})"
            )
        else:
            log(f"  {verdict}: {row.get('details', {}).get('note') or 'no verdict'}")

    card = write_scorecard(batch, label, batch_dir)
    log(
        f"scorecard {batch_dir / 'scorecard.md'}: accuracy {card['accuracy']} over {card['answered']} incidents, "
        f"median time to diagnosis {card['median_seconds']} s"
    )
    return status


if __name__ == "__main__":
    sys.exit(main())
