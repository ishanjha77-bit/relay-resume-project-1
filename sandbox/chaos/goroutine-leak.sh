#!/usr/bin/env bash
# Payments leaks 10 blocked goroutines (64 KB each) per charge until the container is OOMKilled.
# Expected root cause: memory_leak (payments).
source "$(dirname "$0")/lib.sh"
set_flags payments '{"goroutine-leak": {"per_request": 10, "kb": 64}}'
