import { defineConfig, devices } from "@playwright/test";

if (!process.env.LIVE_URL) {
  throw new Error("LIVE_URL must be set — run via `make test-e2e`");
}

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: {
    baseURL: process.env.LIVE_URL,
    headless: true,
    extraHTTPHeaders: {
      Authorization: `Bearer ${process.env.AUTH_TOKEN ?? ""}`,
    },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
