# HighLatency: p95 above one second

HighLatency fires when a service's p95 request latency stays above 1 s for two
minutes. Slowness travels upward: a slow database makes orders slow, and a slow
orders makes the gateway slow. Find the deepest slow component, then why it is
slow.

## Symptoms

- An alert like "orders: p95 latency is 2.3s".
- Callers' outgoing call latency (`http_client_requests_seconds`) rises together
  with the callee's server latency.
- Once latency passes a caller's timeout it turns into errors: the gateway
  returns 504 "upstream request timed out", and HikariCP logs "Connection is not
  available, request timed out after 2000ms".

## Diagnose

1. Locate it. Compare p95 per service and per outgoing call:

   ```
   histogram_quantile(0.95, sum by (service, le) (rate(http_server_requests_seconds_bucket{namespace="sandbox"}[2m])))
   histogram_quantile(0.95, sum by (service, client_name, le) (rate(http_client_requests_seconds_bucket{namespace="sandbox"}[2m])))
   ```

   If one dependency's latency explains a service's latency, move down to that
   dependency.
2. Check whether one endpoint (`uri`) is slow or all of them are. One endpoint
   points at a query or code path (runbook:postgres-slow-queries,
   runbook:postgres-locks). All endpoints point at a shared resource: connection
   pool, threads, CPU or GC.
3. Look for waits on a resource:
   - `hikaricp_connections_pending` > 0: runbook:db-connection-pool
   - `tomcat_threads_busy_threads` at the maximum: runbook:tomcat-threads
   - CPU throttling: runbook:cpu-saturation
   - long GC pauses: runbook:jvm-memory
4. External: if payments' calls to the payment provider are slow, see
   runbook:payment-provider.
5. Changes: check rollout history around the onset.
6. Traffic: compare the request rate now with before. A load increase raises
   latency everywhere at once; a fault raises it in one place.

## Mitigate

- Remove the cause found above. More replicas help only when the bottleneck is
  per pod (CPU, threads), not a shared dependency (the database, the provider).
- Don't raise timeouts to hide latency. Callers then hold threads and
  connections longer, and the slowdown spreads.

## Related

runbook:triage-high-error-rate, runbook:gateway-upstream, runbook:tomcat-threads,
runbook:cpu-saturation
