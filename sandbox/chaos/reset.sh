#!/usr/bin/env bash
# Undo every fault and wait until the sandbox is healthy again.
#
# Only what actually changed is rolled back: a needless rollout would show up in
# the next scenario's deploy history and could mislead the agent.
source "$(dirname "$0")/lib.sh"

log "restoring infrastructure"
kc scale deploy/redis --replicas=1 >/dev/null
kc scale statefulset/postgres --replicas=1 >/dev/null
kc rollout status deploy/redis --timeout=120s >/dev/null
kc rollout status statefulset/postgres --timeout=300s >/dev/null

# Goroutines leaked by payments only go away with the process.
if [[ "$(get_flags payments)" == *goroutine-leak* ]]; then
  log "restarting payments pod (leaked goroutines)"
  kc delete pod -l app.kubernetes.io/name=payments --wait=false >/dev/null
fi
clear_flags
log "cleared runtime flags"

for svc in gateway orders payments inventory; do
  var="${svc^^}_VERSION"
  baseline="${!var}"
  if [[ "$(image_of "$svc")" != "relay/sandbox-$svc:$baseline" ]]; then
    deploy "$svc" "$baseline" "Rollback $svc to $baseline"
  fi
done

restore_env() {
  local svc=$1 key=$2 want=$3
  local have
  have="$(env_value "$svc" "$key")"
  if [[ -z "$want" && -n "$have" ]]; then
    set_env "$svc" "Revert $key on $svc" "$key-"
  elif [[ -n "$want" && "$have" != "$want" ]]; then
    set_env "$svc" "Revert $key on $svc" "$key=$want"
  fi
}
restore_env gateway UPSTREAM_TIMEOUT_MS 4000
restore_env inventory DB_URL jdbc:postgresql://postgres:5432/inventory
restore_env orders INVENTORY_URL http://inventory:8080

psql_sandbox -d orders -c \
  "CREATE INDEX IF NOT EXISTS orders_customer_created_idx ON orders (customer_id, created_at DESC);
   CREATE INDEX IF NOT EXISTS orders_created_idx ON orders (created_at DESC)"

log "waiting for rollouts"
for svc in gateway orders payments inventory; do
  kc rollout status deploy/"$svc" --timeout=300s >/dev/null
done
log "sandbox is back to baseline"
