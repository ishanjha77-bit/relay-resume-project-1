# Slow queries and missing indexes

A query that used to be fast becomes slow when its plan changes: an index was
dropped or can't be used, table statistics are stale, or the data grew. A
sequential scan over a large table on a busy endpoint takes seconds, and holds
a pooled connection the whole time.

## Symptoms

- Latency up on the endpoints that read the affected table (a list of a
  customer's orders, for example), not on every endpoint.
- Postgres CPU and reads up: `pg_stat_database_tup_returned` and
  `pg_stat_database_blks_read` growing much faster than before.
- If slow queries hold connections long enough: pool waits and timeouts
  (runbook:db-connection-pool).

## Diagnose

1. Find the slow endpoint: p95 by `uri` for the affected service.
2. Check what Postgres is doing. A sequential scan returns orders of magnitude
   more rows than an index scan, so watch
   `rate(pg_stat_database_tup_returned[2m])` by `datname`, and per-table
   sequential scans (`pg_stat_user_tables_seq_tup_read` by `relname`) if the
   exporter publishes them.
3. Look in the service's logs for slow-query warnings or statement timeouts.
4. Look for a schema change at the onset: a migration or manual DDL (an index
   dropped, a column altered). DDL doesn't appear in rollout history, so
   compare the timing with the first slow requests instead.
5. Rule out locks. If queries wait rather than work (Postgres CPU stays low),
   see runbook:postgres-locks.

## Mitigate

- Recreate the missing index with `CREATE INDEX CONCURRENTLY`, which doesn't
  block writes. Run ANALYZE on the table if its statistics are stale.
- Shed load on the affected endpoint while the index builds.

## Related

runbook:postgres-locks, runbook:db-connection-pool, runbook:triage-high-latency
