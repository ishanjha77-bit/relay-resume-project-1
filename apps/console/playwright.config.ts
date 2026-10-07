import { defineConfig, devices } from "@playwright/test"

const PORT = Number(process.env.E2E_PORT ?? 3100)

/**
 * End-to-end tests of the console against recorded API responses
 * (e2e/fixtures) and a fake STOMP broker: no backend needed, so they run in CI.
 */
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://localhost:${PORT}`,
    colorScheme: "dark",
    trace: "retain-on-failure",
    // Headless Chromium on some Windows sessions cannot composite with the GPU.
    launchOptions: { args: ["--disable-gpu"] },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    command: `npx next dev -p ${PORT}`,
    url: `http://localhost:${PORT}/login`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
})
