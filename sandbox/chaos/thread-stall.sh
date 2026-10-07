#!/usr/bin/env bash
# 30% of orders requests serialize on a lock for 15 s; Tomcat's 40 worker threads pile up.
# Expected root cause: thread_pool_exhaustion (orders).
source "$(dirname "$0")/lib.sh"
set_flags orders '{"thread-stall": {"ratio": 0.3, "stall_ms": 15000}}'
