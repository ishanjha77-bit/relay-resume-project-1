#!/usr/bin/env bash
# The sandbox Postgres goes away; orders and inventory fail every DB call.
# Expected root cause: dependency_down (postgres).
source "$(dirname "$0")/lib.sh"
kc scale statefulset/postgres --replicas=0 >/dev/null
log 'scaled postgres to 0 replicas'
