# Database connection pool exhaustion (HikariCP)

orders and inventory reach Postgres through HikariCP pools, orders-db and
inventory-db. Each holds at most 10 connections and waits 2 s for a free one.
When every connection is in use, requests queue for one and fail after 2 s. A
pool runs dry for one of three reasons, and each needs a different fix:
connections are leaked (never returned), connections are held too long (slow
queries or locks), or the pool is too small for the traffic (often after a
configuration change).

## Symptoms

- Logs: "orders-db - Connection is not available, request timed out after
  2000ms" (`SQLTransientConnectionException`), with HTTP 500s from the service.
- `hikaricp_connections_active` equal to `hikaricp_connections_max`;
  `hikaricp_connections_pending` above 0; `hikaricp_connections_timeout_total`
  increasing.
- The latency of every DB-backed endpoint rises to about 2 s, then requests
  fail.

## Diagnose

1. Confirm saturation:

   ```
   max by (service) (hikaricp_connections_active{namespace="sandbox"})
   max by (service) (hikaricp_connections_max{namespace="sandbox"})
   max by (service) (hikaricp_connections_pending{namespace="sandbox"})
   ```

2. Tell a leak from slow use. `hikaricp_connections_usage_seconds_max` shows how
   long connections are held.
   - Leak: active climbs to the maximum and stays there even when traffic drops,
     and usage time grows without bound. Postgres sees idle connections rather
     than busy queries (`pg_stat_activity_count` by state). A restart fixes it
     until the pool fills again.
   - Slow use: usage time is high but bounded, and query latency rose first.
     See runbook:postgres-slow-queries and runbook:postgres-locks.
3. Check whether the pool is too small. If `hikaricp_connections_max` is below
   its usual 10, look for a pool-size change in rollout history and in the
   deployment's environment (`SPRING_DATASOURCE_HIKARI_MAXIMUMPOOLSIZE`). A
   shrunken pool saturates under normal traffic with normal query times.
4. Compare the start against rollout history. A leak that begins right after a
   new version is that version's bug.
5. Rule out the database itself. If Postgres were down, the errors would be
   "Connection refused", not pool timeouts (runbook:postgres-unavailable).

## Mitigate

- A leak in a new version: roll back. A restart frees the connections for a
  while.
- A pool shrunk by configuration: restore the previous value (roll back the
  config change).
- Slow queries or locks: fix those. Enlarging the pool moves the bottleneck into
  Postgres.

## Related

runbook:postgres-slow-queries, runbook:postgres-locks, runbook:config-change,
runbook:bad-deploy, runbook:tomcat-threads
