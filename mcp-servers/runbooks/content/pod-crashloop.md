# Pods restarting, crash-looping or not ready

PodCrashLooping fires when a container restarts at least twice in ten minutes.
DeploymentReplicasUnavailable fires when a deployment has had unavailable
replicas for three minutes. Both mean the platform can't keep the service
running. The reason is in the container's last termination state, its logs from
just before the restart, and the deploy history.

## Symptoms

- `kube_pod_container_status_restarts_total` increasing. Pod status
  CrashLoopBackOff, Error or OOMKilled.
- A rollout that doesn't finish: the new ReplicaSet's pods aren't ready while
  the old ReplicaSet keeps serving.
- Probe failures in events ("Readiness probe failed", "Liveness probe failed").

## Diagnose

1. Read the pod status: last terminated reason and exit code.
   - Exit code 137 with OOMKilled: the container exceeded its memory limit
     (runbook:jvm-memory, runbook:go-memory-goroutines).
   - Exit code 1, or any other application exit: the process failed on its own.
     Read its logs from just before the restart.
2. Startup failures show in the first log lines: a host that doesn't resolve
   (`java.net.UnknownHostException`, "no such host"), connection refused by a
   dependency, a missing or malformed setting. If these appeared right after a
   rollout, the rollout introduced them (runbook:config-change,
   runbook:bad-deploy).
3. Compare the failing pods' ReplicaSet with the previous one. Rollout history
   shows what changed (image, environment). While old pods still serve, users
   may see little impact even though the rollout is stuck.
4. Check recent events for scheduling problems (Insufficient memory or cpu),
   image pulls (ErrImagePull, ImagePullBackOff) and probe failures.
5. Restarts without any change point at a leak (memory grows to the limit) or a
   dependency that is down when the service starts (runbook:postgres-unavailable).

## Mitigate

- A bad rollout: roll back to the previous revision.
- OOMKilled from a leak: a restart buys time, and a higher limit only delays the
  next kill. Roll back the change that leaks.
- A dependency is down: fix the dependency, and the pods recover by themselves.

## Related

runbook:config-change, runbook:bad-deploy, runbook:jvm-memory,
runbook:go-memory-goroutines, runbook:postgres-unavailable
