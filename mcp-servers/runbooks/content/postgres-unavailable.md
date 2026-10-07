# Postgres unavailable

orders and inventory each have a database on one shared Postgres instance
(`postgres:5432`). If Postgres is down or unreachable, every DB-backed request
fails fast. There is no pool wait: the connection itself fails.

## Symptoms

- Logs: "Connection to postgres:5432 refused", "PSQLException: The connection
  attempt failed", or `UnknownHostException: postgres` if the Service is gone.
  HikariCP reports "Connection is not available" as the pool drains.
- 5xx from orders and inventory at the same time: both services' DB-backed
  endpoints fail together.
- `pg_up` equal to 0 from the Postgres exporter. The postgres pod is missing,
  not ready or restarting, or its StatefulSet is scaled to 0.

## Diagnose

1. Check whether both database users are failing. If only one service fails,
   the database is probably fine: look at that service's pool or configuration
   (runbook:db-connection-pool, runbook:config-change).
2. Check `pg_up`, and the postgres pod's status and events: scaled down,
   evicted, OOMKilled, storage problems.
3. Read the error type in the logs:
   - connection refused or unknown host: Postgres is down or unreachable;
   - pool timeouts while Postgres is up: pool exhaustion;
   - authentication failures: credentials changed.

## Mitigate

- Restore Postgres: scale the StatefulSet back up, or fix its storage. The
  services reconnect on their own.
- Don't restart the application pods while Postgres is down. They fail
  readiness and end up crash-looping.

## Related

runbook:db-connection-pool, runbook:pod-crashloop, runbook:triage-high-error-rate
