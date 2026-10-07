# Redis cache unavailable or degraded

inventory caches product and stock reads in Redis with a 30 s TTL. When Redis
is unavailable, inventory falls back to Postgres for every read. Answers stay
correct, but each request runs a much more expensive query: latency rises and
database load jumps.

## Symptoms

- inventory logs: `RedisConnectionFailureException`, "Unable to connect to
  Redis", "Command timed out", cache errors followed by a fallback to the
  database.
- inventory p95 latency and Postgres load up, possibly with pool waits on
  inventory-db.
- The redis pod missing, not ready or scaled to 0.

## Diagnose

1. Check inventory's logs for Redis errors, and when they started.
2. Check the redis deployment's status and events: scaled to 0, evicted,
   OOMKilled.
3. Confirm the fallback effect: inventory's database load and pool use rose at
   the same time.
4. Tell it from a database problem. While Redis is healthy, inventory's
   database load is low and steady; a database fault doesn't come with Redis
   errors.

## Mitigate

- Restore Redis (scale it up or restart it). The cache refills within its TTL.
- If Redis can't come back quickly, protect Postgres by rate-limiting
  inventory's reads.

## Related

runbook:triage-high-latency, runbook:db-connection-pool, runbook:postgres-slow-queries
