# 3. Alert on symptoms, never on causes

- Status: accepted
- Date: 2026-10-04

## Context

The sandbox exists to test whether Relay can find root causes. If an alert
were named after its cause ("HikariPoolExhausted", "MemoryLeak"), the alert
itself would be the answer and the evaluation would measure nothing.
This is also how mature SRE teams alert: page on what users feel, investigate
the cause afterwards.

## Decision

The sandbox has exactly four alert rules
(`infra/observability/files/prometheus/sandbox-slo.rules.yml`), all symptoms:

| Alert | Fires when |
|-------|-----------|
| `HighErrorRate` | > 5% of a service's requests return 5xx for 1 min |
| `HighLatency` | a service's p95 latency > 1 s for 2 min |
| `PodCrashLooping` | a container restarts ≥ 2 times in 10 min |
| `DeploymentReplicasUnavailable` | a deployment has unavailable replicas for 3 min |

Alertmanager groups alerts by namespace, so a cascading failure (PSP slow →
payments slow → orders erroring → gateway erroring) arrives as one webhook
with several alerts, not an alert storm.

## Consequences

- The agent must correlate evidence across logs, metrics, deploy history and
  the service graph to name a cause — which is the thing being evaluated.
- One fault usually fires alerts on several services; the alert on the
  *loudest* service is often not where the cause is. Relay must handle that.
- Some faults (a crash-looping new pod behind a healthy old one) only surface
  through the deployment alerts, which is realistic.
