import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end configuration.
 *
 * Both servers are started here rather than assumed: an E2E suite that only
 * passes when a developer happens to have two terminals open is not a check,
 * it is a ritual.
 *
 * The API runs against a temporary SQLite file so a run never touches a real
 * database, and demo mode needs no ingested data at all.
 */
const API_PORT = 8123;
const WEB_PORT = 3123;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  timeout: 60_000,
  expect: { timeout: 15_000 },

  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // The war room is desktop-first: it is used on a laptop beside a draft.
    viewport: { width: 1600, height: 1000 },
  },

  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],

  webServer: [
    {
      command: `cd ../.. && ./.venv/bin/python -m uvicorn fhe.api.app:app --host 127.0.0.1 --port ${API_PORT} --log-level warning`,
      url: `http://127.0.0.1:${API_PORT}/api/v1/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      env: {
        FHE_DATA_DIR: ".e2e-data",
        FHE_CORS_ORIGINS: `http://127.0.0.1:${WEB_PORT},http://localhost:${WEB_PORT}`,
      },
    },
    {
      // `next` directly, not the npm script: that script pins port 3000, and
      // appending another --port would pass both.
      command: `npx next dev --port ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: { NEXT_PUBLIC_API_BASE_URL: `http://127.0.0.1:${API_PORT}` },
    },
  ],
});
