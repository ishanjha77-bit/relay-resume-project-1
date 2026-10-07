# 4. Inject faults with runtime flags, not redeploys

- Status: accepted
- Date: 2026-10-04

## Context

The eval harness injects ~20 kinds of fault. The obvious mechanism — redeploy
a service with a `CHAOS=...` environment variable — has two problems:

1. **It leaks the answer.** A rollout appears in Kubernetes history right
   before the incident, so "it was the last deploy" becomes the correct guess
   for every scenario.
2. **Restarts erase the fault.** A memory leak that disappears when the pod
   restarts isn't a memory leak.

## Decision

Two mechanisms, chosen per scenario:

- **Runtime flags** for faults that live in the running code (connection
  leaks, memory leaks, PSP latency/errors, CPU burn, lock contention…). Each
  service polls `ops:flags:<service>` in the sandbox Redis every 2 s. Flags
  survive restarts (the leak comes back, as it would in production), produce
  no rollout and no log line, and are cleared by `reset.sh`. The fault code
  lives in a `faults/` package that the agent's GitHub tools are not allowed
  to read.
- **Real rollouts** for faults that *are* deploys: a bad release image built
  from a patch in `sandbox/releases/`, or a bad config change. These carry a
  realistic `kubernetes.io/change-cause` and a new `version` label, exactly
  as a CD pipeline would leave them.
- **Infrastructure actions** (scale Redis/Postgres to zero, drop an index)
  where the fault is outside the services.

`reset.sh` rolls back only what changed, because a needless rollout would
pollute the next scenario's deploy history.

## Consequences

- Deploy-history evidence is meaningful: it is present exactly when a deploy
  caused the incident.
- Fault state is visible to anyone with access to the sandbox Redis — fine
  for a test system; the agent has no Redis tool.
- Each Java service carries ~80 lines of fault-injection code that a real
  service would not have.
