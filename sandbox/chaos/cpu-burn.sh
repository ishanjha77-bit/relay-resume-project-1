#!/usr/bin/env bash
# A runaway worker pool in payments: 32 goroutines spin and saturate its 0.5-core CPU limit.
# Payments' own latency stays under its alert threshold; its callers (orders, the gateway) page.
# Expected root cause: cpu_saturation (payments).
source "$(dirname "$0")/lib.sh"
set_flags payments '{"cpu-burn": {"workers": 32}}'
