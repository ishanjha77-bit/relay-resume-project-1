# Go services: goroutine leaks and memory growth

gateway and payments are Go services with a 128 MiB memory limit. Go has no
heap-limit error: memory grows until the kernel kills the container (OOMKilled,
exit code 137). The most common Go leak is goroutines that never exit, blocked
on a channel, a lock or a call without a timeout. Each one holds its stack and
everything it references.

## Symptoms

- `go_goroutines` rising steadily instead of tracking the request rate.
- `go_memstats_heap_inuse_bytes` and `container_memory_working_set_bytes`
  climbing toward `container_spec_memory_limit_bytes`.
- Pod restarts with reason OOMKilled, and PodCrashLooping if they repeat. Errors
  and latency spike around each restart.
- Callers see failures while the pod restarts: connection refused, or 502 from
  the gateway.

## Diagnose

1. Goroutines over time: `max by (service) (go_goroutines{namespace="sandbox"})`.
   In a healthy service the count is stable and proportional to concurrent
   requests. In a leak it climbs and never comes back down.
2. Memory against the limit:
   `container_memory_working_set_bytes / container_spec_memory_limit_bytes` per
   pod.
3. Confirm the kill: the pod's last terminated reason is OOMKilled
   (`kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}`).
4. Find what the goroutines wait on: calls to a dependency that hang without a
   timeout, or contexts that are never cancelled. Check whether a dependency
   became slow at the same time (runbook:payment-provider). A slow dependency
   raises the goroutine count too, but the count falls back when latency
   recovers. A leak doesn't fall back.
5. Check rollout history for a new version or configuration around the start of
   the growth.

## Mitigate

- Roll back the change that leaks. Restarts reset memory and buy time.
- If a slow dependency drives it, fix or bypass the dependency, and give every
  outgoing call a timeout.

## Related

runbook:pod-crashloop, runbook:jvm-memory, runbook:payment-provider
