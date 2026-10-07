#!/usr/bin/env bash
# Redis (inventory's catalog cache) goes away; inventory falls back to an expensive DB query.
# Expected root cause: cache_failure (inventory -> redis).
source "$(dirname "$0")/lib.sh"
kc scale deploy/redis --replicas=0 >/dev/null
log 'scaled redis to 0 replicas'
