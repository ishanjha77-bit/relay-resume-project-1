#!/usr/bin/env bash
# Shared helpers for the chaos scenarios. Every scenario is undone by reset.sh.
set -euo pipefail

CLUSTER="${CLUSTER:-relay}"
CONTEXT="${CONTEXT:-kind-$CLUSTER}"
NS="${SANDBOX_NAMESPACE:-sandbox}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../versions.env
source "$REPO_ROOT/sandbox/versions.env"

# Git Bash on Windows rewrites arguments that look like POSIX paths.
export MSYS_NO_PATHCONV=1

kc() { kubectl --context "$CONTEXT" -n "$NS" "$@"; }

log() { printf '\033[1;35m[chaos]\033[0m %s\n' "$*"; }

# set_flags <service> <json>: replace a service's runtime fault flags. Services
# poll ops:flags:<service> every 2s; nothing is redeployed and nothing is logged.
set_flags() {
  local service=$1 json=$2
  kc exec deploy/redis -- redis-cli SET "ops:flags:$service" "$json" >/dev/null
  log "flags[$service] = $json"
}

get_flags() {
  kc exec deploy/redis -- redis-cli GET "ops:flags:$1" 2>/dev/null || true
}

clear_flags() {
  local s
  for s in gateway orders payments inventory; do
    kc exec deploy/redis -- redis-cli DEL "ops:flags:$s" >/dev/null 2>&1 || true
  done
}

json_escape() {
  local s=${1//\\/\\\\}
  printf '%s' "${s//\"/\\\"}"
}

# The change-cause annotation and the pod-template change go in ONE patch. The
# deployment controller copies Deployment annotations onto whichever ReplicaSet
# is current, so annotating first would rewrite the *previous* revision's
# change-cause and corrupt the deploy history the agent reads.

# deploy <service> <tag> <change-cause>: a real rollout, the way CD would do it.
# The version label changes too, so metrics from the new pods carry version=<tag>.
deploy() {
  local service=$1 tag=$2 cause
  cause="$(json_escape "$3")"
  kc patch deploy/"$service" --type=strategic -p "{
    \"metadata\": {\"annotations\": {\"kubernetes.io/change-cause\": \"$cause\"}},
    \"spec\": {\"template\": {
      \"metadata\": {\"labels\": {\"app.kubernetes.io/version\": \"$tag\"}},
      \"spec\": {\"containers\": [{\"name\": \"$service\", \"image\": \"relay/sandbox-$service:$tag\"}]}}}}" >/dev/null
  log "rolled out $service $tag — \"$3\""
  record_rollout "$service" "$3"
}

# record_rollout <service> <change-cause>: commit the rollout to the deploy repo,
# as CD would have (scripts/deploy_repo.py, standard library only). A no-op
# without Relay's Gitea. Any Python 3 will do: uv's, else the system's (on
# Windows `python3` may be the Store's stub, so it has to prove it runs).
record_rollout() {
  local python=()
  if command -v uv >/dev/null 2>&1; then
    python=(uv run --no-project --quiet python)
  elif python3 -c '' >/dev/null 2>&1; then
    python=(python3)
  elif python -c '' >/dev/null 2>&1; then
    python=(python)
  else
    log "deploy repo not updated: no Python found"
    return 0
  fi
  # Path conversion is off in this file (MSYS_NO_PATHCONV), so hand Windows Python a Windows path.
  local script="$REPO_ROOT/scripts/deploy_repo.py" out attempt
  if command -v cygpath >/dev/null 2>&1; then script="$(cygpath -w "$script")"; fi
  for attempt in 1 2; do
    if out="$("${python[@]}" "$script" record "$1" "$2" 2>&1)"; then
      return 0
    fi
    sleep 2
  done
  log "deploy repo not updated: ${out##*$'\n'}"
}

# set_env <service> <change-cause> KEY=VALUE | KEY- ...: a config-only rollout.
# KEY- removes the variable.
set_env() {
  local service=$1 reason=$2 cause kv
  cause="$(json_escape "$reason")"
  shift 2
  local entries=()
  for kv in "$@"; do
    if [[ "$kv" != *=* && "$kv" == *- ]]; then
      entries+=("{\"name\": \"${kv%-}\", \"\$patch\": \"delete\"}")
    else
      entries+=("{\"name\": \"${kv%%=*}\", \"value\": \"$(json_escape "${kv#*=}")\"}")
    fi
  done
  local joined
  joined="$(IFS=,; printf '%s' "${entries[*]}")"
  kc patch deploy/"$service" --type=strategic -p "{
    \"metadata\": {\"annotations\": {\"kubernetes.io/change-cause\": \"$cause\"}},
    \"spec\": {\"template\": {\"spec\": {\"containers\": [{\"name\": \"$service\", \"env\": [$joined]}]}}}}" >/dev/null
  log "rolled out config change to $service: $* — \"$reason\""
  record_rollout "$service" "$reason"
}

env_value() {
  kc get deploy/"$1" -o jsonpath="{.spec.template.spec.containers[0].env[?(@.name==\"$2\")].value}"
}

image_of() {
  kc get deploy/"$1" -o jsonpath='{.spec.template.spec.containers[0].image}'
}

psql_sandbox() {
  kc exec statefulset/postgres -c postgres -- psql -U postgres -v ON_ERROR_STOP=1 -q "$@"
}
