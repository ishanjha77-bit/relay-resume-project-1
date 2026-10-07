/**
 * The README's demo, recorded: a real incident replayed through the console at a
 * readable pace. The incident and its steps are the platform API's own records
 * (e2e/fixtures/live-*.json from a live run, else demo-*.json, INC-24). Each
 * stage of the incident page is what the platform showed after that many steps:
 * triage, the trace streaming in, the verdict, the review, the fixer asking, the
 * approval, the draft pull request.
 *
 *   DEMO=1 npx playwright test e2e/demo.spec.ts   (the video lands in test-results/)
 */
import { existsSync, readFileSync } from "node:fs"
import path from "node:path"

import { expect, test, type Page } from "@playwright/test"

import type { AgentStep, Approval, Hypothesis, IncidentDetail } from "../lib/types"
import { API, FakeBroker } from "./support"

test.skip(!process.env.DEMO, "records the README demo; set DEMO=1")
test.use({ video: { mode: "on", size: { width: 1280, height: 720 } }, viewport: { width: 1280, height: 720 } })

const fixture = (name: string) => path.join(__dirname, "fixtures", name)
const live = existsSync(fixture("live-incident.json"))
const final = JSON.parse(
  readFileSync(fixture(live ? "live-incident.json" : "demo-incident.json"), "utf-8"),
) as IncidentDetail
const steps = JSON.parse(readFileSync(fixture(live ? "live-steps.json" : "demo-steps.json"), "utf-8")) as AgentStep[]
const id = final.incident.id
// The evals page ends the demo on the real batches, as `make demo-site` exported them.
const evals = (name: string) => {
  const exported = path.join(__dirname, "..", "public", "demo", "evals", `${name}.json`)
  return JSON.parse(readFileSync(existsSync(exported) ? exported : fixture(`eval-${name}.json`), "utf-8")) as unknown
}
const evalBatches = evals("batches")
const evalRuns = evals("runs")
// INC-24 was captured before Gitea linked to its browsable address (`make gitea-ui`).
const executed = JSON.parse(
  JSON.stringify(final.approvals[0] ?? null).replaceAll("http://gitea:3000", "http://localhost:3003"),
) as Approval | null

/** The incident page's data after the first `n` steps of the run. */
function stage(n: number, approved = false): IncidentDetail {
  const seen = new Set(steps.slice(0, n).map((s) => s.kind))
  const at = n > 0 ? steps[n - 1].created_at : final.incident.opened_at
  const status =
    seen.has("approval.requested") && !seen.has("action.executed")
      ? "AWAITING_APPROVAL"
      : seen.has("investigation.concluded")
        ? "DIAGNOSED"
        : seen.has("run.started")
          ? "INVESTIGATING"
          : "OPEN"
  const reviewed = seen.has("review.completed")
  const order = (steps.find((s) => s.kind === "review.completed")?.output?.order as number[] | undefined) ?? []
  // Before the review: the investigator's own ranking and confidence.
  const hypotheses: Hypothesis[] = !seen.has("investigation.concluded")
    ? []
    : reviewed
      ? final.hypotheses
      : final.hypotheses
          .map((h, i) => ({
            ...h,
            rank: order[i] ?? h.rank,
            confidence: h.original_confidence ?? h.confidence,
            original_confidence: null,
            review: null,
          }))
          .sort((a, b) => a.rank - b.rank)
  const pending = executed && {
    ...executed,
    status: approved ? "APPROVED" : "PENDING",
    decided_by: approved ? "alice" : null,
    decided_at: null,
    decision_reason: null,
    result: null,
  }
  return {
    ...final,
    incident: {
      ...final.incident,
      status,
      resolved_at: null,
      root_cause_category: hypotheses[0]?.category ?? null,
      root_cause_service: hypotheses[0]?.service ?? null,
    },
    summary: hypotheses.length ? final.summary : null,
    triage: seen.has("triage.completed") ? final.triage : null,
    hypotheses,
    approvals:
      seen.has("action.executed") && executed
        ? [executed]
        : seen.has("approval.requested") && pending
          ? [pending as Approval]
          : [],
    postmortem: null,
    timeline: final.timeline.filter((e) => e.at <= at && !e.kind.endsWith("resolved")),
  } as IncidentDetail
}

async function mockDemoApi(page: Page, state: { detail: IncidentDetail; steps: AgentStep[] }) {
  await page.route(`${API}/api/**`, async (route) => {
    const p = new URL(route.request().url()).pathname
    const json = (body: unknown) => route.fulfill({ json: body })
    if (p === "/api/auth/token") {
      return json({
        access_token: "demo",
        token_type: "Bearer",
        expires_in: 43_200,
        username: "alice",
        display_name: "Alice Approver",
        roles: ["VIEWER", "RESPONDER", "APPROVER"],
      })
    }
    if (p === "/api/incidents") return json({ items: [state.detail.incident], next_cursor: null })
    if (p.endsWith("/steps")) return json(state.steps)
    // Deciding returns the approval as APPROVED; the pull request comes later, as a step.
    if (p.includes("/approvals/")) return json({ ...executed, status: "APPROVED", result: null })
    if (p.startsWith("/api/incidents/")) return json(state.detail)
    if (p === "/api/evals/batches") return json(evalBatches)
    if (p === "/api/evals/runs") return json(evalRuns)
    return route.fulfill({ status: 404, json: { detail: p } })
  })
}

const PACE: Record<string, number> = {
  "agent.progress": 1_100,
  "tool.called": 450,
  "triage.completed": 2_500,
  "review.completed": 2_000,
}

test("demo: from alert to an approved draft pull request", async ({ page }) => {
  test.setTimeout(240_000)
  const state = { detail: stage(0), steps: [] as AgentStep[] }
  await mockDemoApi(page, state)
  const broker = await FakeBroker.attach(page)
  const topic = `/topic/incidents/${id}`
  const push = (n: number, approved = false) => {
    state.steps = steps.slice(0, n)
    state.detail = stage(n, approved)
    broker.send(topic, { type: "step", step: steps[n - 1] })
    broker.send(topic, { type: "incident", incident: state.detail.incident })
  }

  await page.goto("/login")
  await page.getByLabel("Username").pressSequentially("alice", { delay: 60 })
  await page.getByLabel("Password").pressSequentially("relay-demo", { delay: 40 })
  await page.getByRole("button", { name: "Sign in" }).click()
  await page.waitForURL("**/incidents")
  await page.waitForTimeout(1_500)
  await page.getByRole("listitem").first().click()
  await page.waitForURL(`**/incidents/${id}`)
  await expect.poll(() => broker.subscribed(topic)).toBe(true)
  await page.waitForLoadState("networkidle")

  // Triage, then the investigation streaming in, then the review: up to the fixer's request.
  const asks = steps.findIndex((s) => s.kind === "approval.requested")
  const until = asks >= 0 ? asks : steps.length
  for (let n = 1; n <= until; n++) {
    push(n)
    await page.waitForTimeout(PACE[steps[n - 1].kind] ?? 250)
  }
  await page.waitForTimeout(2_000)
  if (asks < 0 || !executed) return

  // The fixer asks; the evidence behind the verdict; the decision.
  push(asks + 1)
  await expect(page.getByRole("region", { name: /Relay asks to/ })).toBeVisible()
  await page.waitForTimeout(2_500)
  await page
    .getByRole("list", { name: /Root-cause hypotheses/ })
    .getByRole("list", { name: "Evidence" })
    .first()
    .getByRole("button")
    .first()
    .click()
  await page.waitForTimeout(2_500)
  await page.keyboard.press("Escape")
  await page.getByRole("button", { name: "Approve", exact: true }).click()
  await page.getByLabel("Note (optional)").pressSequentially("Errors began with this rollout.", { delay: 35 })
  await page.waitForTimeout(600)
  await page.getByRole("dialog").getByRole("button", { name: "Approve", exact: true }).click()
  await page.waitForTimeout(1_500)

  const done = steps.findIndex((s) => s.kind === "action.executed")
  if (done >= 0) {
    push(done + 1)
    await expect(page.getByRole("link", { name: /Draft pull request/ })).toBeVisible()
  }
  await page.waitForTimeout(3_000)

  // Resolved: the postmortem writer drafts a blameless postmortem from the incident's record.
  if (final.postmortem) {
    state.detail = final
    broker.send(topic, { type: "incident", incident: final.incident })
    await expect(page.getByRole("link", { name: "Postmortem" })).toBeVisible()
    await page.waitForTimeout(1_200)
    await page.getByRole("link", { name: "Postmortem" }).click()
    await page.waitForURL(`**/incidents/${id}/postmortem`)
    await page.waitForTimeout(2_500)
    await page.mouse.wheel(0, 500)
    await page.waitForTimeout(2_500)
  }

  await page.goto("/evals")
  await page.waitForTimeout(3_500)
})
