import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end configuration for offline preview mode.
 *
 * Separate from `playwright.config.ts` because the whole point of this suite is
 * that **no API is running**. Starting one would invalidate every assertion in
 * it: preview must be provably self-sufficient, not merely untested against a
 * backend that happened to be up.
 */
const WEB_PORT = 3124;

export default defineConfig({
  testDir: "./e2e",
  testMatch: /preview\.spec\.ts/,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  workers: 1,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  timeout: 60_000,
  expect: { timeout: 15_000 },

  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    viewport: { width: 1600, height: 1000 },
  },

  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],

  webServer: [
    {
      command: `npx next dev --port ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        NEXT_PUBLIC_PREVIEW_MODE: "fixtures",
        // Deliberately pointed at a port nothing listens on. If any screen
        // still reaches for the network in preview, these tests fail rather
        // than quietly succeeding against a stray local API.
        NEXT_PUBLIC_API_BASE_URL: "http://127.0.0.1:9",
      },
    },
  ],
});
