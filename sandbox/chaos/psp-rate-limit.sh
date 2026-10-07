#!/usr/bin/env bash
# The PSP throttles 70% of charges with 429; payments' retry doubles the load.
# Expected root cause: rate_limited (payments -> psp).
source "$(dirname "$0")/lib.sh"
set_flags payments '{"psp-rate-limit": {"ratio": 0.7}}'
