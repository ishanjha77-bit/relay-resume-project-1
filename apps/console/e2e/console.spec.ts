import AxeBuilder from "@axe-core/playwright"
import { expect, test } from "@playwright/test"

import { approval, FakeBroker, fixtures, mockApi, postmortem, signIn, triage } from "./support"

const incident = fixtures.incident
const firstIncident = fixtures.incidents.items[0]
// The recorded incident as it was while the fixer waited for a decision.
const awaiting = { ...incident, incident: { ...incident.incident, status: "AWAITING_APPROVAL", resolved_at: null } }

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test("signs in and lists incidents with their status and root cause", async ({ page }) => {
  await signIn(page)
  const rows = page.getByRole("listitem")
  await expect(rows).toHaveCount(fixtures.incidents.items.length)
  await expect(rows.first()).toContainText(firstIncident.key)
  await expect(rows.first()).toContainText(firstIncident.title)
})

test("rejects a wrong password", async ({ page }) => {
  await page.goto("/login")
  await page.getByLabel("Password").fill("nope")
  await page.getByRole("button", { name: "Sign in" }).click()
  // Next.js adds its own (empty) role=alert route announcer.
  await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toHaveText("Wrong username or password.")
})

test("j / k and Enter open an incident from the keyboard", async ({ page }) => {
  await signIn(page)
  await expect(page.getByRole("listitem").first()).toBeVisible()
  await page.keyboard.press("j")
  await page.keyboard.press("k")
  await page.keyboard.press("Enter")
  await page.waitForURL(`**/incidents/${firstIncident.id}`)
})

test("the incident page shows the trace, ranked hypotheses and highlighted evidence", async ({ page }) => {
  // Cite a line that really is in the first tool output, so it must be highlighted.
  const step = fixtures.steps.find((s) => s.kind === "tool.called")!
  const output = String((step.output as { output: string }).output)
  const quote = output.slice(10, 60)
  const hypotheses = incident.hypotheses.map((h, i) =>
    i === 0 ? { ...h, evidence: [{ evidence_id: step.evidence_id, quote, shows: "the cited line" }] } : h,
  )
  await mockApi(page, { incident: { ...incident, hypotheses } })
  await signIn(page)
  await page.goto(`/incidents/${incident.incident.id}`)

  await expect(page.getByRole("heading", { level: 1 })).toHaveText(incident.incident.title)
  const trace = page.getByRole("list", { name: "Agent trace" })
  await expect(trace).toContainText("Investigation started")
  await expect(trace).toContainText(step.tool!)

  const ranked = page.getByRole("list", { name: /Root-cause hypotheses/ })
  await expect(ranked.getByRole("listitem").first()).toContainText("Config error")

  // The only chip of the top hypothesis (its evidence was replaced above).
  await ranked.getByRole("button", { name: step.evidence_id! }).first().click()
  const dialog = page.getByRole("dialog")
  await expect(dialog).toContainText(step.tool!)
  await expect(dialog.locator("mark")).toHaveText(quote)
})

test("a step pushed over the WebSocket appears in the trace live", async ({ page }) => {
  const broker = await FakeBroker.attach(page)
  await signIn(page)
  await page.goto(`/incidents/${incident.incident.id}`)
  const topic = `/topic/incidents/${incident.incident.id}`
  await expect.poll(() => broker.subscribed(topic)).toBe(true)
  // Subscribing refetches the steps once; push after that, as a real broker would.
  await page.waitForLoadState("networkidle")

  const last = fixtures.steps.at(-1)!
  broker.send(topic, {
    type: "step",
    step: {
      ...last,
      seq: last.seq + 1,
      kind: "agent.progress",
      tool: null,
      evidence_id: null,
      input: null,
      output: { text: "**Checking the rollout history** of inventory next." },
      created_at: new Date().toISOString(),
    },
  })
  await expect(page.getByRole("list", { name: "Agent trace" })).toContainText("Checking the rollout history", {
    timeout: 20_000,
  })
})

test("a recorded trace replays step by step, and the verdict appears when the replay reaches it", async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page)
  await page.goto(`/incidents/${incident.incident.id}`)
  const ranked = page.getByRole("list", { name: /Root-cause hypotheses/ })
  await expect(ranked).toBeVisible()

  await page.getByRole("button", { name: "Replay" }).click()
  await expect(ranked).toBeHidden()
  await expect(page.getByRole("button", { name: "Stop replay" })).toBeVisible()
  await expect(ranked).toBeVisible({ timeout: 60_000 })
  await expect(page.getByRole("button", { name: "Replay" })).toBeVisible({ timeout: 60_000 })
})

test("the command palette jumps to the evals page", async ({ page }) => {
  await signIn(page)
  await page.keyboard.press("Control+k")
  await page.getByPlaceholder("Search incidents, pages…").fill("Evals")
  await page.keyboard.press("Enter")
  await page.waitForURL("**/evals")
  await expect(page.getByText("Root-cause accuracy")).toBeVisible()
})

test("only responders and approvers can resolve", async ({ page }) => {
  await signIn(page, "vic")
  await page.goto(`/incidents/${incident.incident.id}`)
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible()
  await expect(page.getByRole("button", { name: "Resolve" })).toHaveCount(0)
})

test("an approver approves the change the fixer proposes", async ({ page }) => {
  const decisions: { decision: string; reason: string | null }[] = []
  await mockApi(page, { incident: { ...awaiting, approvals: [approval()] }, decisions })
  await signIn(page)
  await page.goto(`/incidents/${incident.incident.id}`)

  const card = page.getByRole("region", { name: /Relay asks to/ })
  await expect(card).toContainText('Revert "inventory: move to the new database primary (#58)"')
  await expect(card).toContainText("risk: low")
  const diff = card.getByLabel("The change, as a diff")
  await expect(diff).toContainText('+  DB_URL: "jdbc:postgresql://postgres:5432/inventory"')

  await card.getByRole("button", { name: "Approve", exact: true }).click()
  const dialog = page.getByRole("dialog")
  await expect(dialog).toContainText("draft pull request")
  await dialog.getByRole("button", { name: "Approve", exact: true }).click()
  await expect(page.getByText("Approved: the agent will open the pull request")).toBeVisible()
  expect(decisions).toEqual([{ decision: "approve", reason: null }])
})

test("a rejection needs a reason, and only approvers can decide", async ({ page }) => {
  const decisions: { decision: string; reason: string | null }[] = []
  await mockApi(page, { incident: { ...awaiting, approvals: [approval()] }, decisions })
  await signIn(page, "bob")
  await page.goto(`/incidents/${incident.incident.id}`)
  const card = page.getByRole("region", { name: /Relay asks to/ })
  await expect(card.getByRole("button", { name: "Approve", exact: true })).toBeDisabled()
  await expect(card).toContainText("Only someone with the approver role can decide.")

  await page.evaluate(() => sessionStorage.clear()) // sign out bob
  await signIn(page)
  await page.goto(`/incidents/${incident.incident.id}`)
  await card.getByRole("button", { name: "Reject", exact: true }).click()
  const dialog = page.getByRole("dialog")
  const reject = dialog.getByRole("button", { name: "Reject", exact: true })
  await expect(reject).toBeDisabled()
  await dialog.getByLabel("Reason (required)").fill("Wait for the DBA")
  await reject.click()
  await expect(page.getByText("Rejected", { exact: true })).toBeVisible()
  expect(decisions).toEqual([{ decision: "reject", reason: "Wait for the DBA" }])
})

test("an executed approval links to its draft pull request", async ({ page }) => {
  const done = approval({
    status: "EXECUTED",
    decided_by: "alice",
    decided_at: "2026-10-05T10:06:00Z",
    result: { pull_request: 3, url: "http://localhost:3003/shop/deploy/pulls/3", created: true },
  })
  await mockApi(page, { incident: { ...incident, approvals: [done] } })
  await signIn(page)
  await page.goto(`/incidents/${incident.incident.id}`)
  const link = page.getByRole("link", { name: "Draft pull request #3" })
  await expect(link).toHaveAttribute("href", "http://localhost:3003/shop/deploy/pulls/3")
})

test("the postmortem reads as a document and exports as Markdown", async ({ page }) => {
  await mockApi(page, { incident: { ...incident, postmortem: postmortem() } })
  await signIn(page)
  await page.goto(`/incidents/${incident.incident.id}`)
  await page.getByRole("link", { name: "Postmortem" }).click()
  await page.waitForURL(`**/incidents/${incident.incident.id}/postmortem`)

  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "INC-8: inventory pointed at a database that doesn't exist",
  )
  for (const section of ["Summary", "Root cause", "Timeline (UTC)", "Action items", "Lessons"]) {
    await expect(page.getByRole("heading", { level: 2, name: section })).toBeVisible()
  }
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "Export Markdown" }).click(),
  ])
  expect(download.suggestedFilename()).toBe("INC-8-postmortem.md")
  const fs = await import("node:fs/promises")
  expect(await fs.readFile((await download.path())!, "utf-8")).toContain("## Action items")
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze()
  expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([])
})

test("a reviewed hypothesis shows the reviewer's verdict and the lowered confidence", async ({ page }) => {
  const [top, ...rest] = incident.hypotheses
  const reviewed = {
    ...top,
    confidence: 0.7,
    original_confidence: 0.95,
    review: { verdict: "weak", reason: "Only the timing links the change to the errors.", model: "gemini-3.6-flash" },
  }
  await mockApi(page, { incident: { ...incident, hypotheses: [reviewed, ...rest] } })
  await signIn(page)
  await page.goto(`/incidents/${incident.incident.id}`)
  const ranked = page.getByRole("list", { name: /Root-cause hypotheses/ })
  await expect(ranked.getByText("reviewer: weak")).toBeVisible()
  await expect(ranked.getByText("Only the timing links the change to the errors.")).toBeVisible()
  await expect(ranked.getByLabel("Confidence 70%, lowered by the reviewer from 95%")).toBeVisible()
})

test("the triage shows where to start and what the alerts resemble", async ({ page }) => {
  await mockApi(page, { incident: { ...incident, triage: triage() } })
  await signIn(page)
  await page.goto(`/incidents/${incident.incident.id}`)
  const panel = page.getByRole("region", { name: "Triage" })
  await expect(panel.getByText("New inventory pods crash-loop; stock lookups fail for every order.")).toBeVisible()
  await expect(panel.getByRole("list", { name: "Leads" })).toContainText("rollout history")
  const related = panel.getByRole("list", { name: "Related runbooks and past incidents" })
  await expect(related.getByRole("listitem")).toHaveCount(2)
  const map = panel.getByRole("group", { name: /Service map/ })
  await expect(map.getByRole("button", { name: "inventory, alerting" })).toBeVisible()
  const caption = panel.locator("figcaption")
  await expect(caption.getByText("orders → inventory: 4 req/s, 31% failed, p95 0.02 s")).toBeVisible()
  await map.getByRole("button", { name: "gateway" }).focus()
  await expect(caption.getByText("gateway → inventory: 8 req/s, 0% failed, p95 0.01 s")).toBeVisible()
  await related.getByText("orders pointed at the wrong inventory URL").click()
  await expect(related.getByText("INVENTORY_URL was changed to a host that does not exist.")).toBeVisible()
  const results = await new AxeBuilder({ page }).include("section[aria-labelledby=triage-heading]").analyze()
  expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([])
})

test("an evidence chip citing a metric over time carries a mini chart of it", async ({ page }) => {
  const series = {
    window: "last 30 min",
    step_seconds: 15,
    series_count: 1,
    series: [
      {
        labels: { service: "inventory" },
        change: { at: "2026-10-05T10:02:00Z", before: 0.01, after: 0.42 },
        points: [
          ["2026-10-05T10:00:00Z", 0.01],
          ["2026-10-05T10:01:00Z", 0.01],
          ["2026-10-05T10:02:00Z", 0.4],
          ["2026-10-05T10:03:00Z", 0.42],
        ],
      },
    ],
  }
  const cited = incident.hypotheses[0].evidence[0].evidence_id
  const steps = fixtures.steps.map((s) =>
    s.kind === "tool.called" && s.evidence_id === cited
      ? { ...s, tool: "metrics__query_range", output: { ...s.output, output: JSON.stringify(series) } }
      : s,
  )
  await mockApi(page, { steps })
  await signIn(page)
  await page.goto(`/incidents/${incident.incident.id}`)
  const evidence = page.getByRole("list", { name: "Evidence" }).first()
  await expect(evidence.getByRole("img", { name: "inventory: from 0.01 to 0.42, with a level shift" })).toBeVisible()
})

test("responders vote on related runbooks and on hypotheses; viewers only see the votes", async ({ page }) => {
  const votes: unknown[] = []
  const feedback = {
    documents: { "runbook:crashloop": { up: 2, down: 0, mine: 0 } },
    hypotheses: {},
  }
  await mockApi(page, { incident: { ...incident, triage: triage(), feedback }, votes })
  await signIn(page)
  await page.goto(`/incidents/${incident.incident.id}`)

  const helpful = page.getByRole("group", { name: "Was Pods crash-loop after a rollout helpful?" })
  await expect(helpful.getByRole("button", { name: /helpful/ }).first()).toHaveText(/2/)
  await helpful.getByRole("button", { name: "2 helpful" }).click()
  const [top] = incident.hypotheses
  await page
    .getByRole("group", { name: /the root cause\?/ })
    .first()
    .getByRole("button", { name: /not the root cause/ })
    .click()
  await expect.poll(() => votes.length).toBe(2)
  expect(votes).toEqual([
    { document: "runbook:crashloop", vote: "up" },
    { hypothesis: { run_id: top.run_id, category: top.category, service: top.service }, vote: "down" },
  ])

  await page.evaluate(() => sessionStorage.clear()) // sign out
  await signIn(page, "vic")
  await page.goto(`/incidents/${incident.incident.id}`)
  await expect(
    page.getByRole("group", { name: "Was Pods crash-loop after a rollout helpful?" }).getByRole("button").first(),
  ).toBeDisabled()
})

test("a request still pending when the incident was resolved shows that nothing ran", async ({ page }) => {
  await mockApi(page, { incident: { ...incident, approvals: [approval()] } })
  await signIn(page)
  await page.goto(`/incidents/${incident.incident.id}`)
  await expect(page.getByText("Not decided: the incident was resolved first, so nothing ran.")).toBeVisible()
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toHaveCount(0)
})

test("no serious accessibility violations on the approval card", async ({ page }) => {
  await mockApi(page, { incident: { ...awaiting, approvals: [approval()] } })
  await signIn(page)
  await page.goto(`/incidents/${incident.incident.id}`)
  await expect(page.getByRole("region", { name: /Relay asks to/ })).toBeVisible()
  await page.waitForLoadState("networkidle")
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze()
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")
  expect(serious.map((v) => `${v.id}: ${v.nodes.length} node(s)`)).toEqual([])
})

for (const path of ["/login", "/incidents", `/incidents/${incident.incident.id}`, "/evals"]) {
  test(`no serious accessibility violations on ${path.replace(/[0-9a-f-]{36}/, "[id]")}`, async ({ page }) => {
    if (path !== "/login") await signIn(page)
    await page.goto(path)
    await page.waitForLoadState("networkidle")
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze()
    const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")
    expect(serious.map((v) => `${v.id}: ${v.nodes.length} node(s)`)).toEqual([])
  })
}
