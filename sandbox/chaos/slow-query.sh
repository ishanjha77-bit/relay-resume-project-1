#!/usr/bin/env bash
# An "unused index" cleanup drops both indexes on orders. GET /orders?customerId=...
# becomes a 2M-row sequential scan; the scans saturate the database's CPU, so
# inventory's queries slow down too. (With only the composite index gone the planner
# walks the created_at index instead, which is merely slower.)
# Expected root cause: slow_query (orders / postgres).
source "$(dirname "$0")/lib.sh"
psql_sandbox -d orders -c 'DROP INDEX IF EXISTS orders_customer_created_idx; DROP INDEX IF EXISTS orders_created_idx'
log 'dropped indexes orders_customer_created_idx and orders_created_idx'
