#!/usr/bin/env bash
# The external payment provider (PSP) answers in 2.5-3.3 s. Orders times out on payments after 2 s.
# Expected root cause: dependency_latency (payments -> psp).
source "$(dirname "$0")/lib.sh"
set_flags payments '{"psp-latency": {"delay_ms": 2500, "jitter_ms": 800, "ratio": 1.0}}'
