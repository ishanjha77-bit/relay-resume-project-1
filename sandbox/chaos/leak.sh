#!/usr/bin/env bash
# Orders retains ~384 KB per request; the heap fills, the JVM dies with OutOfMemoryError and restarts.
# Expected root cause: memory_leak (orders).
source "$(dirname "$0")/lib.sh"
set_flags orders '{"memory-leak": {"kb_per_request": 384}}'
