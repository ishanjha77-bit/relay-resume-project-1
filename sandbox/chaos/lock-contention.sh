#!/usr/bin/env bash
# Inventory's stock-reconciliation job holds row locks on the 20 hottest SKUs for 3 s per pass.
# Expected root cause: lock_contention (inventory).
source "$(dirname "$0")/lib.sh"
set_flags inventory '{"stock-reconciliation": {"hold_ms": 3000}}'
