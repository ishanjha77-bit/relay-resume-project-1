#!/usr/bin/env bash
# End to end through the deployed system, the way an incident really flows:
# inject a fault -> Prometheus alert -> Alertmanager -> platform-api opens an
# incident -> agent-service investigates through the MCP servers -> verdict
# stored and streamed. Prints the incident, then does what on-call would:
# mitigate (reset the sandbox), wait for the alerts to clear, resolve.
# Needs `make up`; with an API key, each run costs one investigation.
#
#   scripts/smoke.sh [scenario]        (default: db-pool)
set -euo pipefail
cd "$(dirname "$0")/.."

scenario="${1:-db-pool}"
api="${RELAY_API:-http://localhost:8081}"
alertmanager="${ALERTMANAGER_URL:-http://localhost:9093}"
password="${RELAY_DEMO_PASSWORD:-relay-demo}"
[ -f "sandbox/chaos/$scenario.sh" ] || { echo "unknown scenario: $scenario"; exit 1; }

field() { uv run --no-project python -c "import json, sys; d = json.load(sys.stdin); print($1)"; }

token="$(curl -sf -X POST "$api/api/auth/token" -H 'Content-Type: application/json' \
  -d "{\"username\": \"alice\", \"password\": \"$password\"}" | field 'd["access_token"]')"
get() { curl -sf -H "Authorization: Bearer $token" "$api$1"; }

# Alerts are grouped per namespace, so an open incident would absorb the new alerts.
open="$(get '/api/incidents?status=active' | field '" ".join(i["key"] for i in d["items"])')"
if [ -n "$open" ]; then
  echo "Resolve the open incident(s) first: $open"
  exit 1
fi

trap 'echo; echo "resetting the sandbox..."; bash sandbox/chaos/reset.sh > /dev/null 2>&1 || echo "reset failed: run make reset"' EXIT
started=$(date +%s)
bash "sandbox/chaos/$scenario.sh"

echo "waiting for the alert to open an incident (about 2 minutes)..."
id=""
for _ in $(seq 1 90); do
  id="$(get '/api/incidents?status=active' | field '(d["items"] or [{}])[0].get("id", "")' || true)"
  [ -n "$id" ] && break
  sleep 5
done
[ -n "$id" ] || { echo "no incident after 7.5 minutes: check Alertmanager ($alertmanager)"; exit 1; }
echo "incident opened after $(( $(date +%s) - started )) s; waiting for the agent..."

status=""
for _ in $(seq 1 150); do
  status="$(get "/api/incidents/$id" | field 'd["incident"]["status"]' || true)"
  case "$status" in DIAGNOSED | AWAITING_APPROVAL | FAILED) break ;; esac
  sleep 5
done
echo "verdict after $(( $(date +%s) - started )) s"
echo
get "/api/incidents/$id" | uv run --no-project python scripts/incident_report.py

echo
echo "mitigating: resetting the sandbox..."
trap - EXIT
bash sandbox/chaos/reset.sh > /dev/null 2>&1 || echo "reset failed: run make reset"
echo "waiting for the alerts to clear..."
for _ in $(seq 1 60); do
  firing="$(curl -sf "$alertmanager/api/v2/alerts?active=true&silenced=false&inhibited=false" \
    | field 'sum(1 for a in d if a["labels"].get("namespace") == "sandbox")' || true)"
  [ "$firing" = 0 ] && break
  sleep 10
done
curl -sf -X POST -H "Authorization: Bearer $token" "$api/api/incidents/$id/resolve" > /dev/null
echo "alerts clear after $(( $(date +%s) - started )) s; incident resolved."
[ "$status" = DIAGNOSED ] || [ "$status" = AWAITING_APPROVAL ]
