# Request thread exhaustion (Tomcat)

orders and inventory serve HTTP on Tomcat with 40 worker threads and an accept
queue of 50. If requests block (on a lock, a slow call, a full connection pool),
the threads run out. New requests wait in the queue, latency rises in steps, and
callers time out.

## Symptoms

- `tomcat_threads_busy_threads` equal to `tomcat_threads_config_max_threads`
  (40).
- `jvm_threads_states_threads{state="blocked"}`, or the waiting and
  timed-waiting states, rising.
- p95 latency climbing on all endpoints, and the gateway logging 504 "upstream
  request timed out". CPU is usually low, because the threads are waiting, not
  working.

## Diagnose

1. Compare busy threads with the maximum:
   `max by (service) (tomcat_threads_busy_threads{namespace="sandbox"})` and
   `tomcat_threads_config_max_threads`.
2. Find what the threads wait for:
   - Blocked threads rising: contention on a Java lock (synchronized code), so
     requests serialize.
   - Pool pending above 0: the DB connection pool (runbook:db-connection-pool).
   - Slow outgoing calls: a dependency (runbook:payment-provider,
     runbook:triage-high-latency).
3. If blocked threads rise with no slow dependency and no pool wait, the service
   itself serializes requests: a lock held during slow work. This usually starts
   with a deploy or a configuration change, so check rollout history.

## Mitigate

- Roll back the change that introduced the lock or the blocking call.
- A restart clears the queue only for a while.
- More threads make lock contention worse.

## Related

runbook:db-connection-pool, runbook:triage-high-latency, runbook:gateway-upstream
