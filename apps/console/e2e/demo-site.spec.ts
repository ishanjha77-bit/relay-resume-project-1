/**
 * The public read-only demo, as `make demo-site` builds it, served as static files:
 *
 *   python -m http.server 3200 -d apps/console/out
 *   DEMO_SITE_URL=http://localhost:3200 npx playwright test e2e/demo-site.spec.ts
 */
import { expect, test } from "@playwright/test"

const SITE = process.env.DEMO_SITE_URL
test.skip(!SITE, "checks a built demo site; set DEMO_SITE_URL")

test("visitors browse real recorded incidents, read-only", async ({ page }) => {
  await page.goto(`${SITE}/incidents/`)
  await expect(page.getByText(/Read-only demo/)).toBeVisible()
  const incidents = page.getByRole("list", { name: "all incidents" }).getByRole("listitem")
  await expect(incidents.first()).toBeVisible()
  expect(await incidents.count()).toBeGreaterThan(10)

  await incidents.first().click()
  await page.waitForURL(/\/incidents\/[0-9a-f-]{36}\/?$/)
  await expect(page.getByRole("list", { name: "Agent trace" })).toBeVisible()
  await expect(page.getByRole("list", { name: /Root-cause hypotheses/ })).toBeVisible()
  await expect(page.getByRole("button", { name: "Resolve" })).toHaveCount(0) // visitors are viewers

  await page.getByRole("button", { name: "Replay" }).click()
  await expect(page.getByRole("list", { name: /Root-cause hypotheses/ })).toBeHidden()
  await expect(page.getByRole("button", { name: "Stop replay" })).toBeVisible()

  await page.goto(`${SITE}/evals/`)
  await expect(page.getByText(/Calibration/).first()).toBeVisible()
})
