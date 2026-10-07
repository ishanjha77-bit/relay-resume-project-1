import assert from "node:assert/strict"
import type { AddressInfo } from "node:net"
import { after, before, describe, it } from "node:test"

import type { CoreV1Event, V1Deployment, V1Pod, V1ReplicaSet } from "@kubernetes/client-node"
import { Client } from "@modelcontextprotocol/sdk/client/index.js"
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js"

import { hostAllowed, start, tokenMatches } from "../src/server.ts"
import { redactEnv, toJson } from "../src/shape.ts"
import { type Kube, K8sTools } from "../src/tools.ts"

const NOW = new Date("2026-10-05T10:30:00Z")
const minutesAgo = (m: number) => new Date(NOW.getTime() - m * 60_000)

const labels = (service: string, version: string) => ({ "app.kubernetes.io/name": service, "app.kubernetes.io/version": version })

function replicaSet(revision: number, version: string, cause: string, env: { name: string; value: string }[], created: Date): V1ReplicaSet {
  return {
    metadata: {
      name: `orders-${revision}`,
      creationTimestamp: created,
      annotations: { "deployment.kubernetes.io/revision": String(revision), "kubernetes.io/change-cause": cause },
      ownerReferences: [{ apiVersion: "apps/v1", kind: "Deployment", name: "orders", uid: "orders-uid" }],
    },
    spec: {
      selector: {},
      template: {
        metadata: { labels: labels("orders", version) },
        spec: { containers: [{ name: "orders", image: `relay/sandbox-orders:${version}`, env }] },
      },
    },
    status: { replicas: revision === 3 ? 1 : 0, readyReplicas: revision === 3 ? 1 : 0 },
  }
}

/** The bad-deploy scenario, as the cluster would show it. */
const cluster: Kube = {
  deployments: async () =>
    [
      {
        metadata: { name: "orders", uid: "orders-uid", labels: labels("orders", "1.4.0"), annotations: { "deployment.kubernetes.io/revision": "3" } },
        spec: {
          replicas: 1,
          selector: {},
          template: {
            spec: {
              containers: [
                {
                  name: "orders",
                  image: "relay/sandbox-orders:1.4.0",
                  env: [
                    { name: "DB_URL", value: "jdbc:postgresql://orders:hunter2@postgres:5432/orders" },
                    { name: "DB_PASSWORD", value: "hunter2" },
                    { name: "API_TOKEN", valueFrom: { secretKeyRef: { name: "s", key: "k" } } },
                    { name: "DB_POOL_SIZE", value: "10" },
                  ],
                  resources: { limits: { memory: "512Mi" } },
                },
              ],
            },
          },
        },
        status: { readyReplicas: 1 },
      },
      { metadata: { name: "inventory", uid: "inv-uid", labels: labels("inventory", "1.5.1") }, spec: { selector: {}, template: {} } },
    ] as V1Deployment[],
  replicaSets: async () => [
    replicaSet(2, "1.3.0", "Release orders 1.3.0", [{ name: "DB_POOL_SIZE", value: "10" }], minutesAgo(600)),
    replicaSet(3, "1.4.0", "Release orders 1.4.0", [{ name: "DB_POOL_SIZE", value: "10" }, { name: "FEATURE_CODES", value: "on" }], minutesAgo(12)),
    { ...replicaSet(1, "1.2.0", "Release orders 1.2.0", [], minutesAgo(900)), metadata: { annotations: {}, ownerReferences: [{ apiVersion: "apps/v1", kind: "Deployment", name: "other", uid: "other" }] } },
  ],
  pods: async () =>
    [
      {
        metadata: { name: "orders-7f9c", labels: labels("orders", "1.4.0") },
        status: {
          phase: "Running",
          startTime: minutesAgo(3),
          containerStatuses: [
            {
              name: "orders",
              image: "x",
              imageID: "x",
              ready: false,
              restartCount: 4,
              state: { waiting: { reason: "CrashLoopBackOff" } },
              lastState: { terminated: { reason: "OOMKilled", exitCode: 137, finishedAt: minutesAgo(1) } },
            },
          ],
        },
      },
      { metadata: { name: "inventory-1", labels: labels("inventory", "1.5.1") }, status: { phase: "Running", containerStatuses: [] } },
    ] as V1Pod[],
  events: async () =>
    [
      { metadata: {}, involvedObject: { kind: "Pod", name: "orders-7f9c" }, type: "Warning", reason: "BackOff", message: "Back-off restarting failed container", count: 6, lastTimestamp: minutesAgo(2) },
      { metadata: {}, involvedObject: { kind: "Pod", name: "orders-7f9c" }, type: "Normal", reason: "Pulled", message: "Container image pulled", lastTimestamp: minutesAgo(3) },
      { metadata: {}, involvedObject: { kind: "Pod", name: "inventory-1" }, type: "Warning", reason: "Unhealthy", message: "Readiness probe failed", lastTimestamp: minutesAgo(90) },
    ] as CoreV1Event[],
}

const tools = new K8sTools(cluster, "sandbox", () => NOW)

describe("k8s tools", () => {
  it("lists deployments with versions and availability", async () => {
    const { deployments } = await tools.listDeployments()
    assert.deepEqual(
      deployments.map((d) => [d.service, d.version, d.revision]),
      [
        ["inventory", "1.5.1", 0],
        ["orders", "1.4.0", 3],
      ],
    )
  })

  it("shows what each rollout changed, newest first", async () => {
    const history = await tools.rolloutHistory("orders")
    assert.equal(history.current_revision, 3)
    assert.deepEqual(
      history.revisions.map((r) => [r.revision, r.version, r.current, r.created]),
      [
        [3, "1.4.0", true, "12m ago"],
        [2, "1.3.0", false, "10h ago"],
      ],
    ) // the revision owned by another deployment is not included
    assert.deepEqual(history.revisions[0].changed, [
      "image relay/sandbox-orders:1.3.0 -> relay/sandbox-orders:1.4.0",
      "env FEATURE_CODES added: on",
    ])
    assert.equal(history.revisions[0].change_cause, "Release orders 1.4.0")
  })

  it("explains unhealthy pods", async () => {
    const { pods } = await tools.podStatus("orders")
    assert.equal(pods.length, 1)
    assert.deepEqual(
      { restarts: pods[0].restarts, waiting: pods[0].waiting, last: pods[0].last_termination },
      { restarts: 4, waiting: "CrashLoopBackOff", last: { reason: "OOMKilled", exit_code: 137, finished: "60s ago" } },
    )
  })

  it("returns recent warnings for a service", async () => {
    const recent = await tools.recentEvents("orders", 30)
    assert.deepEqual(recent.events.map((e) => [e.reason, e.count]), [["BackOff", 6]])
    const everything = await tools.recentEvents(undefined, 120, false)
    assert.equal(everything.events.length, 3)
  })

  it("masks secrets in configuration", async () => {
    const config = await tools.deploymentConfig("orders")
    assert.deepEqual(config.env, [
      { name: "DB_URL", value: "jdbc:postgresql://orders:[redacted]@postgres:5432/orders" },
      { name: "DB_PASSWORD", value: "[redacted]" },
      { name: "API_TOKEN", from: "secret" },
      { name: "DB_POOL_SIZE", value: "10" },
    ])
    await assert.rejects(() => tools.deploymentConfig("nope"), /no deployment "nope"/)
  })

  it("shapes output: redaction and a size cap", () => {
    assert.equal(redactEnv("GITHUB_TOKEN", "ghp_x"), "[redacted]")
    assert.match(toJson({ text: "x".repeat(100) }, 40), /clipped/)
  })
})

describe("the MCP server", () => {
  const token = "test-token"
  let url: string
  let close: () => void

  before(async () => {
    const server = start(tools, { port: 0, token, allowedHosts: ["127.0.0.1:*"] })
    await new Promise((resolve) => server.once("listening", resolve))
    url = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
    close = () => server.close()
  })
  after(() => close())

  it("serves read-only tools to a client with the token", async () => {
    const client = new Client({ name: "test", version: "1" })
    await client.connect(
      new StreamableHTTPClientTransport(new URL(`${url}/mcp`), { requestInit: { headers: { Authorization: `Bearer ${token}` } } }),
    )
    const { tools: listed } = await client.listTools()
    assert.deepEqual(listed.map((t) => t.name).sort(), [
      "deployment_config",
      "list_deployments",
      "pod_status",
      "recent_events",
      "rollout_history",
    ])
    assert.ok(listed.every((t) => t.annotations?.readOnlyHint === true))
    const result = await client.callTool({ name: "rollout_history", arguments: { service: "orders" } })
    const text = (result.content as { text: string }[])[0].text
    assert.equal(JSON.parse(text).revisions[0].version, "1.4.0")
    await client.close()
  })

  it("refuses a missing token and a foreign Host header", async () => {
    const init = { method: "POST", headers: { "content-type": "application/json", accept: "application/json, text/event-stream" }, body: "{}" }
    assert.equal((await fetch(`${url}/mcp`, init)).status, 401)
    assert.equal((await fetch(`${url}/healthz`)).status, 200)
    assert.equal(hostAllowed("evil.example:80", ["127.0.0.1:*"]), false)
    assert.equal(tokenMatches("Bearer nope", token), false)
    assert.equal(tokenMatches(`Bearer ${token}`, token), true)
  })
})
