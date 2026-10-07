#!/usr/bin/env bash
# Orders leaks pooled DB connections on ~20% of requests until HikariCP is exhausted.
# Expected root cause: db_pool_exhaustion (orders).
source "$(dirname "$0")/lib.sh"
set_flags orders '{"db-connection-leak": {"ratio": 0.2}}'
