#!/usr/bin/env bash
# Inventory is pointed at a database host that doesn't exist; new pods crash-loop and the rollout stalls.
# Expected root cause: config_error (inventory).
source "$(dirname "$0")/lib.sh"
set_env inventory 'inventory: move to the new primary database endpoint (#164)' DB_URL=jdbc:postgresql://postgres-primary:5432/inventory
