# JVM heap exhaustion and OutOfMemoryError

orders and inventory are Spring Boot services on the JVM with a 512 MiB
container limit. When live objects keep growing, the garbage collector runs
more often and frees less each time. Latency climbs, and finally the JVM throws
`java.lang.OutOfMemoryError` or the container is OOMKilled.

## Symptoms

- "java.lang.OutOfMemoryError: Java heap space" (or "GC overhead limit
  exceeded") in the logs, with requests failing with 500s around it.
- `jvm_memory_used_bytes{area="heap"}` rising toward `jvm_memory_max_bytes` and
  not dropping after collections; `jvm_gc_pause_seconds_sum` growing quickly.
- Restarts with reason OOMKilled or Error. Memory resets after each restart and
  climbs again.

## Diagnose

1. Plot heap use over the incident window:

   ```
   sum by (service) (jvm_memory_used_bytes{namespace="sandbox", area="heap"})
   sum by (service) (jvm_memory_max_bytes{namespace="sandbox", area="heap"})
   ```

   A leak looks like a sawtooth whose lows keep rising. Normal load is a
   sawtooth with a flat floor.
2. Correlate growth with traffic. Memory retained per request (an unbounded
   cache, a list that is never cleared) grows faster when traffic is higher.
3. Check GC time with `rate(jvm_gc_pause_seconds_sum[2m])`. A JVM that spends
   more than about 10% of its time in GC is close to failing.
4. Check restarts and the last termination reason. OOMKilled means the
   container limit was hit (heap plus non-heap). OutOfMemoryError in the logs
   means the heap limit was hit.
5. Check rollout history. Growth that starts with a new version or a
   configuration change points at that change. If nothing changed, look for a
   change in traffic: a new endpoint being hit, larger payloads.

## Mitigate

- Roll back the change that introduced the growth. Restarts only reset the
  clock.
- A larger heap or limit only delays the failure when the cause is a leak.

## Related

runbook:pod-crashloop, runbook:go-memory-goroutines, runbook:cpu-saturation
