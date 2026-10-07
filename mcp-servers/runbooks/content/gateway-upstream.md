# Gateway 502 and 504 responses

The gateway proxies `/api` requests to orders and inventory, with one upstream
timeout for all routes (`UPSTREAM_TIMEOUT_MS`, normally 4000 ms). It answers 502
"upstream request failed" when it can't reach an upstream, and 504 "upstream
request timed out" when the upstream doesn't answer in time. It logs every
failure with the timeout and the error.

## Symptoms

- HighErrorRate on gateway. The gateway logs "upstream request timed out" (504,
  with `timeout_ms`) or "upstream request failed" (502).
- 504s: the upstream is slow, or the timeout is too short for its normal
  latency.
- 502s: the upstream is down or refuses connections (crash loop, rollout, pods
  not ready).

## Diagnose

1. Find which upstream fails and with which status, from the gateway's logs.
2. For 504s, compare the upstream's own p95 latency with `timeout_ms`.
   - The upstream is fast (p95 well below the timeout) yet the gateway times
     out: the timeout is too short. Check the gateway's configuration and its
     rollout history.
   - The upstream is slow: follow the upstream (runbook:triage-high-latency).
3. For 502s, check the upstream's pods (runbook:pod-crashloop).
4. Check the upstream's own error rate. If it reports no errors while the
   gateway fails, the problem is between them (the gateway's configuration or
   the network), not in the upstream.

## Mitigate

- A timeout that is too short: restore the previous value.
- A slow or down upstream: fix the upstream. Raising the gateway's timeout
  hides the problem.

## Related

runbook:config-change, runbook:triage-high-latency, runbook:pod-crashloop
