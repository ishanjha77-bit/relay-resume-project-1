#!/usr/bin/env python3
"""Export incidents Relay investigated, exactly as the platform API recorded them,
for the console's public read-only demo (apps/console/public/demo).

The demo is a static build of the console (`make demo-site`) that answers its API
calls from these files: the incidents of the chosen eval batches with every agent
step, the eval batches and runs. Nothing is written if any file looks like it
holds a secret. Standard library only.

  python scripts/demo_site.py                                  # the latest eval batch
  python scripts/demo_site.py --batches 2026-10-05-baseline,2026-10-06-after --extra INC-24
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

API = os.environ.get("RELAY_API", "http://localhost:8081")
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "apps" / "console" / "public" / "demo"

# What must never be published: keys and tokens of the services Relay talks to.
SECRETS = {
    "Google API key": re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    "Anthropic key": re.compile(r"sk-ant-[0-9A-Za-z_\-]{20,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[0-9A-Za-z]{30,}"),
    "JWT": re.compile(r"eyJ[0-9A-Za-z_\-]{10,}\.eyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


def own_secrets() -> list[str]:
    """Relay's own secrets (the relay Secret and .env): Gitea tokens look like any commit
    hash, so they are compared by value. Read here, compared below, never printed."""
    values: list[str] = []
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
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout
        values += [base64.b64decode(v).decode(errors="ignore") for v in json.loads(out)["data"].values()]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        print("warning: could not read the relay Secret; checking key patterns only", file=sys.stderr)
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                values.append(line.split("=", 1)[1].strip().strip("\"'"))
    return [v.strip() for v in values if len(v.strip()) >= 12]


class Api:
    def __init__(self) -> None:
        self.token = self._post(
            "/api/auth/token",
            {"username": "alice", "password": os.environ.get("RELAY_DEMO_PASSWORD", "relay-demo")},
        )["access_token"]

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        request = urllib.request.Request(
            API + path, json.dumps(body).encode(), {"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    def get(self, path: str) -> Any:
        request = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--batches", help="comma-separated eval batches whose incidents to include (default: the latest)"
    )
    parser.add_argument(
        "--extra", default="", help="comma-separated incident keys to include too, e.g. INC-24"
    )
    args = parser.parse_args()

    api = Api()
    batches = api.get("/api/evals/batches")
    chosen = args.batches.split(",") if args.batches else [batches[0]["batch"]] if batches else []
    runs = api.get("/api/evals/runs?limit=1000")
    ids = {r["incident_id"] for r in runs if r.get("batch") in chosen and r.get("incident_id")}
    everything = api.get("/api/incidents?status=all&limit=200")["items"]
    extra = set(filter(None, args.extra.split(",")))
    ids |= {i["id"] for i in everything if i["key"] in extra}
    incidents = [i for i in everything if i["id"] in ids]
    if not incidents:
        print(f"no incidents found for batches {chosen} and {sorted(extra)}", file=sys.stderr)
        return 1

    files: dict[str, Any] = {
        "incidents.json": {"items": incidents, "next_cursor": None},
        "evals/batches.json": batches,
        "evals/runs.json": runs,
    }
    for incident in incidents:
        files[f"incidents/{incident['id']}.json"] = api.get(f"/api/incidents/{incident['id']}")
        files[f"steps/{incident['id']}.json"] = api.get(f"/api/incidents/{incident['id']}/steps?limit=1000")

    rendered = {
        name: json.dumps(body, separators=(",", ":"), ensure_ascii=False) for name, body in files.items()
    }
    leaks = [
        (name, kind)
        for name, text in rendered.items()
        for kind, pattern in SECRETS.items()
        if pattern.search(text)
    ]
    own = own_secrets()
    leaks += [
        (name, "secret of this Relay") for name, text in rendered.items() if any(s in text for s in own)
    ]
    if leaks:
        for name, kind in leaks:
            print(f"refusing to export: {name} contains something that looks like a {kind}", file=sys.stderr)
        return 2

    shutil.rmtree(OUT, ignore_errors=True)
    for name, text in rendered.items():
        path = OUT / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    size = sum(len(t.encode()) for t in rendered.values())
    keys = ", ".join(i["key"] for i in sorted(incidents, key=lambda i: int(i["key"].split("-")[-1])))
    print(
        f"exported {len(incidents)} incidents ({keys}) and {len(runs)} eval runs: {size / 1e6:.1f} MB in {OUT}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
