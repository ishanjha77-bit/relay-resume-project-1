#!/usr/bin/env bash
# A config change cuts the gateway's upstream timeout to 80 ms; checkout requests start returning 504.
# Expected root cause: config_error (gateway).
source "$(dirname "$0")/lib.sh"
set_env gateway 'gateway: tighten upstream timeouts to protect the edge (#87)' UPSTREAM_TIMEOUT_MS=80
