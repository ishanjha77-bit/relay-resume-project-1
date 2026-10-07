#!/usr/bin/env bash
# A config change points orders at a port inventory doesn't listen on. Checkouts fail
# ("Connection refused" calling inventory) while inventory itself serves the gateway's
# browse traffic just fine: the fault is orders' configuration, not inventory.
# Expected root cause: config_error (orders).
source "$(dirname "$0")/lib.sh"
set_env orders 'orders: call inventory through its new internal port (#227)' INVENTORY_URL=http://inventory:8081
