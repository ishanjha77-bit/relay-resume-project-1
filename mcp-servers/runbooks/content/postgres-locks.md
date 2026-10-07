# Row lock contention in Postgres

Writers that update the same rows queue behind each other: each UPDATE waits
until the transaction holding the row lock commits. A long transaction on
popular rows (a batch job, a reconciliation, a migration) makes every request
that touches those rows wait, while requests for other rows stay fast.

## Symptoms

- Latency up on write endpoints that touch the same records, such as stock
  reservations for the most popular SKUs. Reads stay fast.
- `pg_locks_count` elevated, `pg_stat_activity_count{state="active"}` up, and
  `pg_stat_activity_max_tx_duration` high.
- Pool usage time up, because connections are held while they wait. In extreme
  cases there are lock timeouts or deadlocks (`pg_stat_database_deadlocks`).
- Logs like "canceling statement due to lock timeout" or "deadlock detected".

## Diagnose

1. Look for long transactions: `pg_stat_activity_max_tx_duration` by `datname`.
   A transaction open for tens of seconds on a busy database is suspicious.
2. Look at lock counts: `pg_locks_count` by `mode` and `datname`.
3. Find the work that holds the locks: application logs for batch or scheduled
   jobs that started near the onset, and which endpoints slowed down (they
   share the locked rows).
4. Tell it from slow queries. Lock waits burn no CPU: Postgres CPU stays normal
   while latency climbs.

## Mitigate

- Stop or postpone the job holding the locks, and make it commit in small
  batches.
- If needed, cancel the blocking session (`pg_cancel_backend`).

## Related

runbook:postgres-slow-queries, runbook:db-connection-pool, runbook:triage-high-latency
