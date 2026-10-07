# HighErrorRate: a service is returning 5xx

Start here when HighErrorRate fires: more than 5% of a service's requests
returned 5xx over two minutes. The alerting service is often not where the
problem is. The gateway reports the errors of everything behind it, and orders
reports the errors of payments and inventory. Find where the errors start before
deciding why.

## Symptoms

- An alert like "orders: 12% of requests are failing".
- `http_server_requests_seconds_count{outcome="SERVER_ERROR"}` rising for one or
  more services.
- Callers log failed calls. The gateway logs "upstream request failed" (502) or
  "upstream request timed out" (504).

## Diagnose

1. Find the origin. Calls flow gateway → orders and inventory; orders →
   inventory and payments; payments → the payment provider (external); orders
   and inventory → Postgres; inventory → Redis. Compare each service's own 5xx
   ratio with the errors of its outgoing calls:

   ```
   sum by (service) (rate(http_server_requests_seconds_count{namespace="sandbox", outcome="SERVER_ERROR"}[2m]))
     / sum by (service) (rate(http_server_requests_seconds_count{namespace="sandbox"}[2m]))
   sum by (service, client_name, status) (rate(http_client_requests_seconds_count{namespace="sandbox"}[2m]))
   ```

   The deepest service whose own errors rose is the origin. The services above
   it are reporting symptoms.
2. Read the origin's error logs for the first new error signature (exception
   class or message) and note when it first appeared. Ignore IDs: one signature
   repeated thousands of times is one cause.
3. Look for a change just before that time: rollout history (a new image or a
   changed environment variable), restarts, recent events. A change shortly
   before the first error is the strongest lead. A change after it is not a
   cause.
4. Check the origin for saturation: DB connection pool
   (`hikaricp_connections_pending`, active vs max), request threads
   (`tomcat_threads_busy_threads` vs `tomcat_threads_config_max_threads`), CPU
   throttling, memory and restarts.
5. Check its dependencies: Postgres, Redis, the payment provider (status codes
   and latency of payments' outgoing calls).
6. Confirm with the specific runbook for the signature you found. Each lists the
   log lines and metrics that confirm its cause or rule it out.

## Mitigate

- A recent change matches the onset: roll it back (runbook:bad-deploy,
  runbook:config-change).
- A dependency is the origin: mitigate there. Restarting the callers doesn't
  help and adds load.
- A resource is exhausted: a restart only buys time. Find what holds the
  resource.

## Related

runbook:triage-high-latency, runbook:gateway-upstream, runbook:db-connection-pool,
runbook:payment-provider, runbook:pod-crashloop
