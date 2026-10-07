#!/usr/bin/env bash
# 60% of PSP charges fail with 503; payments retries once, then returns 502.
# Expected root cause: dependency_errors (payments -> psp).
source "$(dirname "$0")/lib.sh"
set_flags payments '{"psp-errors": {"ratio": 0.6}}'
