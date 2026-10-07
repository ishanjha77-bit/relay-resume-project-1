#!/usr/bin/env bash
# Create or update the `relay-secrets` Secret that infra/helm/relay reads.
#
#   gemini-api-key      from GEMINI_API_KEY (environment, else .env); refreshed every run
#   anthropic-api-key   from ANTHROPIC_API_KEY, likewise; only claude-* models need it
#   alertmanager-token  must match the observability chart's alertmanager.webhookToken
#   jwt-private-key     RSA key (PKCS#8) that signs the platform API's tokens
#   postgres-password, webhook-secret, mcp-logs-token, mcp-metrics-token, mcp-k8s-token,
#                       mcp-runbooks-token, mcp-github-token, agent-api-token, gitea-admin-password
#   github-token        from GITHUB_TOKEN; only for deployRepo.host=github
#   gitea-*-token       the deploy repo's bot tokens, created by `make deploy-repo`
#
# Generated values are created once and then kept, so tokens, sessions and the
# database password survive redeploys. Values are never printed, and are written
# only to a private temp directory that is removed on exit.
set -euo pipefail
cd "$(dirname "$0")/.."

CONTEXT="${CONTEXT:-kind-relay}"
NAMESPACE="${NAMESPACE:-relay}"
SECRET=relay-secrets
kc() { kubectl --context "$CONTEXT" -n "$NAMESPACE" "$@"; }

# kubectl is a native Windows program under Git Bash: give it Windows paths.
native_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

dir="$(mktemp -d)"
trap 'rm -rf "$dir"' EXIT
chmod 700 "$dir"

kubectl --context "$CONTEXT" create namespace "$NAMESPACE" --dry-run=client -o yaml \
  | kubectl --context "$CONTEXT" apply -f - >/dev/null

existing() {
  kc get secret "$SECRET" -o "jsonpath={.data.$1}" 2>/dev/null | base64 -d 2>/dev/null || true
}

# keep <key> <generator...>: the current value, or a newly generated one.
keep() {
  local key="$1"; shift
  local value
  value="$(existing "$key")"
  if [ -n "$value" ]; then
    printf '%s' "$value" > "$dir/$key"
    echo "  $key: kept"
  else
    "$@" > "$dir/$key"
    echo "  $key: generated"
  fi
}

# openssl in Git Bash ends lines with CRLF: strip the CR too, or it lands inside the value.
token() { openssl rand -hex 24 | tr -d '\r\n'; }
rsa_key() { openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 2>/dev/null | tr -d '\r'; }
alertmanager_token() { printf '%s' "${RELAY_ALERTMANAGER_TOKEN:-dev-alertmanager-token}"; }

echo "Secret $NAMESPACE/$SECRET:"
keep jwt-private-key rsa_key
keep postgres-password token
keep webhook-secret token
keep mcp-logs-token token
keep mcp-metrics-token token
keep mcp-k8s-token token
keep mcp-runbooks-token token
keep mcp-github-token token
keep gitea-admin-password token
# Created by `make deploy-repo` (scripts/deploy_repo.py) once Gitea runs: carried over, never generated here.
carry() { local value; value="$(existing "$1")"; if [ -n "$value" ]; then printf '%s' "$value" > "$dir/$1"; fi; }
carry gitea-ci-token
carry gitea-relay-token
keep agent-api-token token
keep alertmanager-token alertmanager_token

# from_env NAME: the variable from the environment, else its last value in .env (quotes stripped).
from_env() {
  local value="${!1:-}"
  if [ -z "$value" ] && [ -f .env ]; then
    value="$(sed -n "s/^$1=//p" .env | tail -n 1 | tr -d '\r' | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/")"
  fi
  printf '%s' "$value"
}

# API keys: refreshed from .env on every run, so a new key is one `make relay-secrets` away.
gemini="$(from_env GEMINI_API_KEY)"
printf '%s' "$gemini" > "$dir/gemini-api-key"
if [ -n "$gemini" ]; then echo "  gemini-api-key: from .env"; else echo "  gemini-api-key: NOT SET (free key: https://aistudio.google.com/apikey)"; fi
anthropic="$(from_env ANTHROPIC_API_KEY)"
printf '%s' "$anthropic" > "$dir/anthropic-api-key"
if [ -n "$anthropic" ]; then echo "  anthropic-api-key: from .env"; else echo "  anthropic-api-key: not set (only claude-* models need it)"; fi
github="$(from_env GITHUB_TOKEN)"
if [ -n "$github" ]; then printf '%s' "$github" > "$dir/github-token"; echo "  github-token: from .env"; fi

kc create secret generic "$SECRET" --from-file="$(native_path "$dir")" --dry-run=client -o yaml \
  | kc apply -f - >/dev/null
echo "applied."
