import { defineConfig, devices } from "@playwright/test";

// Runs against an already running stack: `make up` (default) or a dev server.
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.TORQRUN_E2E_BASE_URL ?? "http://127.0.0.1:8080",
    trace: "retain-on-failure",
    // The API's CSRF guard: API calls made with the `request` fixture use the session cookie too.
    extraHTTPHeaders: { "X-Torqrun-Client": "e2e" },
  },
  projects: [
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], storageState: "e2e/.auth/admin.json" },
      dependencies: ["setup"],
    },
  ],
});
