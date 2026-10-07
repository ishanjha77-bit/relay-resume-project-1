/**
 * What the agents can learn from Kubernetes, read-only and scoped to one
 * namespace: what is deployed, what changed and when, which pods are unwell
 * and why, and how a deployment is configured (secrets masked).
 */
import type { CoreV1Event, V1Container, V1Deployment, V1Pod, V1ReplicaSet } from "@kubernetes/client-node"

import { ago, redactEnv } from "./shape.ts"

/** The cluster, as far as these tools need it (a fake in tests). */
export interface Kube {
  deployments(namespace: string): Promise<V1Deployment[]>
  replicaSets(namespace: string): Promise<V1ReplicaSet[]>
  pods(namespace: string): Promise<V1Pod[]>
  events(namespace: string): Promise<CoreV1Event[]>
}

const SERVICE_LABEL = "app.kubernetes.io/name"
const VERSION_LABEL = "app.kubernetes.io/version"
const REVISION = "deployment.kubernetes.io/revision"
const CHANGE_CAUSE = "kubernetes.io/change-cause"

export class K8sTools {
  private readonly kube: Kube
  readonly namespace: string
  private readonly now: () => Date

  constructor(kube: Kube, namespace: string, now: () => Date = () => new Date()) {
    this.kube = kube
    this.namespace = namespace
    this.now = now
  }

  /** Every deployment with its version and availability. */
  async listDeployments() {
    const deployments = await this.kube.deployments(this.namespace)
    return {
      namespace: this.namespace,
      deployments: deployments
        .map((d) => ({
          service: name(d),
          version: d.metadata?.labels?.[VERSION_LABEL],
          image: d.spec?.template.spec?.containers[0]?.image,
          replicas: {
            desired: d.spec?.replicas ?? 0,
            ready: d.status?.readyReplicas ?? 0,
            unavailable: d.status?.unavailableReplicas ?? 0,
          },
          revision: Number(d.metadata?.annotations?.[REVISION] ?? 0),
        }))
        .sort((a, b) => a.service.localeCompare(b.service)),
    }
  }

  /** The deployment's revisions, newest first: what changed, to what, when and why. */
  async rolloutHistory(service: string, limit = 5) {
    const deployment = await this.deployment(service)
    const uid = deployment.metadata?.uid
    const current = Number(deployment.metadata?.annotations?.[REVISION] ?? 0)
    const now = this.now()
    const revisions = (await this.kube.replicaSets(this.namespace))
      .filter((rs) => rs.metadata?.ownerReferences?.some((o) => o.uid === uid))
      .map((rs) => {
        const container = rs.spec?.template?.spec?.containers[0]
        const created = rs.metadata?.creationTimestamp
        return {
          revision: Number(rs.metadata?.annotations?.[REVISION] ?? 0),
          current: Number(rs.metadata?.annotations?.[REVISION] ?? 0) === current,
          version: rs.spec?.template?.metadata?.labels?.[VERSION_LABEL],
          image: container?.image,
          change_cause: rs.metadata?.annotations?.[CHANGE_CAUSE],
          env: summarizeEnv(container),
          created_at: created ? new Date(created).toISOString() : undefined,
          created: ago(created, now),
          replicas: rs.status?.replicas ?? 0,
          ready: rs.status?.readyReplicas ?? 0,
        }
      })
      .sort((a, b) => b.revision - a.revision)
    // What changed between consecutive revisions, so the model does not have to diff.
    const withChanges = revisions.map((r, i) => ({ ...r, changed: changes(revisions[i + 1], r) }))
    return { service, current_revision: current, revisions: withChanges.slice(0, limit) }
  }

  /** Pods and their health: restarts, waiting reasons, how the last container died. */
  async podStatus(service?: string) {
    const now = this.now()
    const pods = (await this.kube.pods(this.namespace)).filter((p) => !service || p.metadata?.labels?.[SERVICE_LABEL] === service)
    return {
      pods: pods
        .map((p) => {
          const status = p.status?.containerStatuses?.[0]
          const last = status?.lastState?.terminated
          return {
            pod: p.metadata?.name,
            service: p.metadata?.labels?.[SERVICE_LABEL],
            version: p.metadata?.labels?.[VERSION_LABEL],
            phase: p.status?.phase,
            ready: status?.ready ?? false,
            restarts: status?.restartCount ?? 0,
            waiting: status?.state?.waiting?.reason,
            started: ago(p.status?.startTime, now),
            last_termination: last
              ? { reason: last.reason, exit_code: last.exitCode, finished: ago(last.finishedAt, now) }
              : undefined,
          }
        })
        .sort((a, b) => (a.service ?? "").localeCompare(b.service ?? "") || b.restarts - a.restarts),
    }
  }

  /** Recent cluster events (warnings by default): OOM kills, failed probes, back-offs, scheduling. */
  async recentEvents(service?: string, minutes = 30, warningsOnly = true) {
    const now = this.now()
    const since = now.getTime() - minutes * 60_000
    const events = (await this.kube.events(this.namespace))
      .map((e) => ({ event: e, at: new Date(e.lastTimestamp ?? e.eventTime ?? e.metadata?.creationTimestamp ?? 0) }))
      .filter(({ event, at }) => at.getTime() >= since)
      .filter(({ event }) => !warningsOnly || event.type === "Warning")
      .filter(({ event }) => !service || (event.involvedObject?.name ?? "").startsWith(`${service}-`) || event.involvedObject?.name === service)
      .sort((a, b) => b.at.getTime() - a.at.getTime())
    return {
      window_minutes: minutes,
      events: events.slice(0, 40).map(({ event, at }) => ({
        at: at.toISOString(),
        type: event.type,
        reason: event.reason,
        object: `${event.involvedObject?.kind}/${event.involvedObject?.name}`,
        message: event.message,
        count: event.count ?? 1,
      })),
      more: Math.max(0, events.length - 40),
    }
  }

  /** How a deployment runs: image, resources, probes and environment (secrets masked). */
  async deploymentConfig(service: string) {
    const deployment = await this.deployment(service)
    const container = deployment.spec?.template.spec?.containers[0]
    return {
      service,
      version: deployment.metadata?.labels?.[VERSION_LABEL],
      image: container?.image,
      replicas: deployment.spec?.replicas,
      resources: container?.resources,
      env: (container?.env ?? []).map((e) =>
        e.valueFrom
          ? { name: e.name, from: e.valueFrom.secretKeyRef ? "secret" : e.valueFrom.configMapKeyRef ? "configmap" : "field" }
          : { name: e.name, value: redactEnv(e.name, e.value) },
      ),
      probes: {
        readiness: container?.readinessProbe?.httpGet?.path,
        liveness: container?.livenessProbe?.httpGet?.path,
      },
    }
  }

  private async deployment(service: string): Promise<V1Deployment> {
    const deployment = (await this.kube.deployments(this.namespace)).find((d) => name(d) === service)
    if (!deployment) throw new Error(`no deployment ${JSON.stringify(service)} in namespace ${this.namespace}`)
    return deployment
  }
}

function name(d: V1Deployment): string {
  return d.metadata?.labels?.[SERVICE_LABEL] ?? d.metadata?.name ?? "?"
}

function summarizeEnv(container: V1Container | undefined): Record<string, string | undefined> {
  return Object.fromEntries((container?.env ?? []).filter((e) => !e.valueFrom).map((e) => [e.name, redactEnv(e.name, e.value)]))
}

type Revision = { version?: string; image?: string; env: Record<string, string | undefined> }

/** Human-readable differences from the previous revision. */
function changes(previous: Revision | undefined, current: Revision): string[] {
  if (!previous) return []
  const out: string[] = []
  if (previous.image !== current.image) out.push(`image ${previous.image} -> ${current.image}`)
  for (const key of new Set([...Object.keys(previous.env), ...Object.keys(current.env)])) {
    const before = previous.env[key]
    const after = current.env[key]
    if (before !== after) {
      if (before === undefined) out.push(`env ${key} added: ${after}`)
      else if (after === undefined) out.push(`env ${key} removed (was ${before})`)
      else out.push(`env ${key}: ${before} -> ${after}`)
    }
  }
  return out
}
