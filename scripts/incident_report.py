"""Print an incident from GET /api/incidents/{id} (JSON on stdin) for a terminal."""

import json
import sys
from datetime import datetime

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")


def when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


detail = json.load(sys.stdin)
incident = detail["incident"]
opened, updated = when(incident["opened_at"]), when(incident["updated_at"])

print(f"{incident['key']}  {incident['title']}")
print(f"status {incident['status']} · severity {incident['severity']} · run {detail.get('run_id')}")
if incident.get("root_cause_category"):
    print(f"root cause: {incident['root_cause_category']} in {incident['root_cause_service']}")
if detail.get("summary"):
    print(f"summary: {detail['summary']}")
cost = incident.get("cost_usd") or 0
elapsed = f"{(updated - opened).total_seconds():.0f} s after the first alert" if opened and updated else "?"
print(f"cost ${float(cost):.4f} · verdict {elapsed}")

for h in detail.get("hypotheses", []):
    print(
        f"  #{h['rank'] + 1} {h['category']} in {h['service']} ({h['component']}), "
        f"confidence {float(h['confidence']):.2f}, citations {h['verdict']}"
    )
    print(f"     {h['summary']}")

print("timeline:")
for event in detail.get("timeline", []):
    print(f"  {event['at'][11:19]}  {event['kind']:<18} {event['message']}")
