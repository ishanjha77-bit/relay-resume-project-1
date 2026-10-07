import type { Page, WebSocketRoute } from "@playwright/test"

import incident from "./fixtures/incident.json"
import incidents from "./fixtures/incidents.json"
import steps from "./fixtures/steps.json"
import evalBatches from "./fixtures/eval-batches.json"
import evalRuns from "./fixtures/eval-runs.json"

export const API = "http://localhost:8081"
export const fixtures = { incident, incidents, steps, evalBatches, evalRuns }

const NUL = String.fromCharCode(0)
const LF = String.fromCharCode(10)

const DIFF = [
  "diff --git a/services/inventory.yaml b/services/inventory.yaml",
  "--- a/services/inventory.yaml",
  "+++ b/services/inventory.yaml",
  "@@ -4,1 +4,1 @@",
  '-  DB_URL: "jdbc:postgresql://postgres-primary:5432/inventory"',
  '+  DB_URL: "jdbc:postgresql://postgres:5432/inventory"',
  "",
].join(LF)

/** What the fixer asked for on the recorded incident, in the platform's shape. */
export function approval(overrides: Record<string, unknown> = {}) {
  return {
    id: "9b2e4c1a-5d6f-4a7b-8c9d-0e1f2a3b4c5d",
    incident_id: incident.incident.id,
    run_id: incident.run_id,
    kind: "revert_change",
    title: 'Revert "inventory: move to the new database primary (#58)"',
    risk: "low",
    rationale: "The new DB_URL points at a host that does not exist, so new inventory pods crash at startup.",
    diff: DIFF,
    action: JSON.stringify({ kind: "revert_change", repo: "shop/deploy", commit: "c".repeat(40) }),
    action_sha256: "4d5f2a0e9c1b7a3f".repeat(4),
    requested_by_agent: "fixer",
    requested_at: "2026-10-05T10:04:00Z",
    status: "PENDING",
    decided_by: null,
    decision_reason: null,
    decided_at: null,
    result: null,
    error: null,
    ...overrides,
  }
}

/** A postmortem of the recorded incident, in the platform's shape. */
export function postmortem() {
  return {
    incident_id: incident.incident.id,
    run_id: "pm-0123456789ab",
    title: "inventory pointed at a database that doesn't exist",
    model: "gemini-3.5-flash-lite",
    written_at: "2026-10-05T10:30:00Z",
    markdown: [
      "# INC-8: inventory pointed at a database that doesn't exist",
      "",
      "## Action items",
      "",
      "- [ ] **high** Validate DB_URL before rollout",
    ].join(LF),
    document: {
      title: "inventory pointed at a database that doesn't exist",
      summary: "A config change pointed inventory at postgres-primary, which doesn't resolve; new pods crash-looped.",
      impact: "New inventory pods never became ready; the old ones kept serving.",
      detection: "PodCrashLooping fired on inventory about two minutes after the rollout.",
      root_cause: "DB_URL was changed to a host that does not exist.",
      resolution: "The config change was reverted.",
      timeline: [
        { at: "2026-10-05T10:01:00Z", event: "inventory: move to the new primary database endpoint (#164)." },
        { at: "2026-10-05T10:03:10Z", event: "PodCrashLooping fired on inventory." },
      ],
      action_items: [{ item: "Validate DB_URL before rollout.", owner: "platform team", priority: "high" }],
      lessons: ["The rollout history pointed straight at the change."],
    },
  }
}

/** The triage of the recorded incident, in the platform's shape. */
export function triage() {
  return {
    run_id: incident.run_id,
    severity: "critical",
    service: "inventory",
    summary: "New inventory pods crash-loop; stock lookups fail for every order.",
    leads: ["Check inventory's rollout history for a configuration change (runbook:crashloop)."],
    query: "PodCrashLooping inventory",
    related: [
      {
        doc_id: "runbook:crashloop",
        kind: "runbook",
        title: "Pods crash-loop after a rollout",
        section: "Diagnose",
        score: 0.0328,
        text: "Compare the failing pods' environment with the previous ReplicaSet's.",
      },
      {
        doc_id: "postmortem:INC-3",
        kind: "postmortem",
        title: "orders pointed at the wrong inventory URL",
        section: "Root cause",
        score: 0.0301,
        text: "INVENTORY_URL was changed to a host that does not exist.",
      },
    ],
    model: "gemini-3.5-flash-lite",
    errors: [],
    dependencies: [
      { from: "user", to: "gateway", rps: 14.1, failed_ratio: 0.12, p95_s: 0.2 },
      { from: "gateway", to: "orders", rps: 6.0, failed_ratio: 0.2, p95_s: 0.1 },
      { from: "gateway", to: "inventory", rps: 8.0, failed_ratio: 0.0, p95_s: 0.01 },
      { from: "orders", to: "inventory", rps: 4.0, failed_ratio: 0.31, p95_s: 0.02 },
      { from: "orders", to: "payments", rps: 4.0, failed_ratio: 0.0, p95_s: 0.05 },
    ],
    upstream: ["user"],
    downstream: ["payments"],
  }
}

/** The platform API, answered from recorded responses. Decisions posted to it are kept in `decisions`. */
export async function mockApi(
  page: Page,
  overrides: {
    incident?: unknown
    steps?: unknown
    decisions?: { decision: string; reason: string | null }[]
    votes?: unknown[]
  } = {},
) {
  await page.route(`${API}/api/**`, async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body })
    if (path === "/api/auth/token") {
      const { username, password } = route.request().postDataJSON() as { username: string; password: string }
      if (password !== "relay-demo") return json({ detail: "bad credentials" }, 401)
      return json({
        access_token: "test-token",
        token_type: "Bearer",
        expires_in: 43_200,
        username,
        display_name: username === "alice" ? "Alice Approver" : username,
        roles:
          username === "vic"
            ? ["VIEWER"]
            : username === "bob"
              ? ["VIEWER", "RESPONDER"]
              : ["VIEWER", "RESPONDER", "APPROVER"],
      })
    }
    if (path === "/api/incidents") return json(incidents)
    if (path.endsWith("/steps")) return json(overrides.steps ?? steps)
    if (path.endsWith("/resolve")) return route.fulfill({ status: 204 })
    if (path.includes("/approvals/") && route.request().method() === "POST") {
      const body = route.request().postDataJSON() as { decision: string; reason: string | null }
      overrides.decisions?.push(body)
      const status = body.decision === "approve" ? "APPROVED" : "REJECTED"
      return json(approval({ status, decided_by: "alice", decision_reason: body.reason }))
    }
    if (path.endsWith("/feedback") && route.request().method() === "POST") {
      overrides.votes?.push(route.request().postDataJSON())
      return route.fulfill({ status: 204 })
    }
    if (path.startsWith("/api/incidents/")) return json(overrides.incident ?? incident)
    if (path === "/api/evals/batches") return json(evalBatches)
    if (path === "/api/evals/runs") return json(evalRuns)
    return json({ detail: `no fixture for ${path}` }, 404)
  })
}

/** A STOMP frame: command, headers, a blank line, the body, a NUL byte. */
function frame(command: string, headers: Record<string, string | number>, body = ""): string {
  const lines = [command, ...Object.entries(headers).map(([k, v]) => `${k}:${v}`)]
  return lines.join(LF) + LF + LF + body + NUL
}

/**
 * A minimal STOMP 1.2 broker on the console's WebSocket: accepts CONNECT,
 * remembers SUBSCRIBEs, and lets a test push MESSAGE frames to a topic.
 */
export class FakeBroker {
  // destination -> the socket that subscribed, and its subscription id
  private subscriptions = new Map<string, { ws: WebSocketRoute; id: string }>()
  private sequence = 0

  static async attach(page: Page): Promise<FakeBroker> {
    const broker = new FakeBroker()
    // One handler per connection: React's dev-mode double mount opens (and closes) an extra one.
    await page.routeWebSocket(/[/]ws$/, (ws) => {
      ws.onMessage((raw) => broker.receive(ws, String(raw)))
      ws.onClose(() => {
        for (const [destination, sub] of broker.subscriptions) {
          if (sub.ws === ws) broker.subscriptions.delete(destination)
        }
      })
    })
    return broker
  }

  private receive(ws: WebSocketRoute, raw: string) {
    for (const chunk of raw.split(NUL)) {
      const [head] = chunk.split(LF + LF)
      const [command, ...headerLines] = head
        .split(LF)
        .map((line) => line.trim())
        .filter(Boolean)
      const headers = Object.fromEntries(
        headerLines.map((line) => [line.slice(0, line.indexOf(":")), line.slice(line.indexOf(":") + 1)]),
      )
      if (command === "CONNECT" || command === "STOMP") {
        ws.send(frame("CONNECTED", { version: "1.2", "heart-beat": "0,0" }))
      } else if (command === "SUBSCRIBE") {
        this.subscriptions.set(headers.destination, { ws, id: headers.id })
      } else if (command === "UNSUBSCRIBE") {
        for (const [destination, sub] of this.subscriptions) {
          if (sub.ws === ws && sub.id === headers.id) this.subscriptions.delete(destination)
        }
      }
    }
  }

  subscribed(destination: string): boolean {
    return this.subscriptions.has(destination)
  }

  send(destination: string, body: unknown) {
    const sub = this.subscriptions.get(destination)
    if (!sub) throw new Error(`nobody subscribed to ${destination}`)
    sub.ws.send(
      frame(
        "MESSAGE",
        {
          destination,
          subscription: sub.id,
          "message-id": ++this.sequence,
          "content-type": "application/json",
        },
        JSON.stringify(body),
      ),
    )
  }
}

export async function signIn(page: Page, username = "alice") {
  await page.goto("/login")
  await page.getByLabel("Username").fill(username)
  await page.getByLabel("Password").fill("relay-demo")
  await page.getByRole("button", { name: "Sign in" }).click()
  await page.waitForURL("**/incidents")
}
