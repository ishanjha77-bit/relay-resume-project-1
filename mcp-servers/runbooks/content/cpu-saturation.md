# CPU saturation and throttling

Every container in the shop has a CPU limit: 500m for the Go services, one core
for the Java services. A container that wants more CPU than its limit is
throttled by the kernel's CFS quota, which pauses it for the rest of each
100 ms period. Throttling shows up as latency on every endpoint, usually
without errors.

## Symptoms

- Latency up on every endpoint of one service; HighLatency alerts with few or
  no errors.
- `container_cpu_usage_seconds_total` close to the limit, and
  `container_cpu_cfs_throttled_periods_total` / `container_cpu_cfs_periods_total`
  above about 25%.
- Under heavy throttling, health checks can time out and cause restarts.

## Diagnose

1. Throttling ratio per pod:

   ```
   sum by (pod) (rate(container_cpu_cfs_throttled_periods_total{namespace="sandbox"}[2m]))
     / sum by (pod) (rate(container_cpu_cfs_periods_total{namespace="sandbox"}[2m]))
   ```

2. CPU use against the limit:
   `sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="sandbox", container!=""}[2m]))`.
3. Decide whether it is load. Compare the request rate now with before.
   - CPU per request constant and traffic up: it's load.
   - Traffic flat and CPU up: the service does more work per request, or burns
     CPU in the background (a busy loop, a hot retry loop, heavy GC in the JVM
     services, see runbook:jvm-memory).
4. Look for runaway workers: the goroutine count (Go) or thread states (JVM).
5. Check rollout history. A new version that burns CPU starts at the rollout.

## Mitigate

- Roll back a change that introduced the CPU burn.
- Saturation from load: scale out (more replicas) or raise the limit.

## Related

runbook:triage-high-latency, runbook:jvm-memory, runbook:go-memory-goroutines
