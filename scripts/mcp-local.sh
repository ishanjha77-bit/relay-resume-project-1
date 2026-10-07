#!/usr/bin/env bash
# Run the MCP servers on this machine against the kind cluster's Loki,
# Prometheus, API server and Relay's Postgres (published on localhost by
# infra/kind-cluster.yaml). Ctrl-C stops them all.
set -euo pipefail
cd "$(dirname "$0")/.."

export LOKI_URL="${LOKI_URL:-http://localhost:3100}"
export PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}"
export LOG_LEVEL="${LOG_LEVEL:-WARNING}"

trap 'kill 0' EXIT INT TERM

PORT=8101 MCP_TOKEN=dev-logs-token MCP_PUBLIC_URL=http://localhost:8101/mcp \
  uv run --package relay-mcp-logs-loki relay-mcp-logs-loki &
PORT=8102 MCP_TOKEN=dev-metrics-token MCP_PUBLIC_URL=http://localhost:8102/mcp \
  uv run --package relay-mcp-metrics-prometheus relay-mcp-metrics-prometheus &
# The TypeScript server reads the cluster through your kubeconfig (context kind-relay).
(cd mcp-servers/k8s-readonly && [ -d node_modules ] || npm ci --silent) && \
  PORT=8103 MCP_TOKEN=dev-k8s-token SANDBOX_NAMESPACE=sandbox \
  node --experimental-strip-types --disable-warning=ExperimentalWarning mcp-servers/k8s-readonly/src/main.ts &

# The knowledge base lives in Relay's Postgres; its password is in the cluster's secret.
PGPASSWORD="$(kubectl --context kind-relay -n relay get secret relay-secrets -o 'jsonpath={.data.postgres-password}' | base64 -d)" \
  PORT=8104 MCP_TOKEN=dev-runbooks-token MCP_PUBLIC_URL=http://localhost:8104/mcp \
  DATABASE_URL=postgresql://relay@localhost:15432/relay \
  uv run --package relay-mcp-runbooks relay-mcp-runbooks &

echo "MCP servers: logs :8101 · metrics :8102 · k8s :8103 · runbooks :8104 (http://localhost:<port>/mcp)"
wait
