import { defineConfig } from "@playwright/test";

// Run through scripts/verify_web.sh, which serves the built page from an API
// on this machine and passes its address and a key for this run.
export default defineConfig({
  testDir: "e2e",
  timeout: 90_000,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.STOCKDC_WEB_URL ?? "http://127.0.0.1:8766",
    browserName: "chromium",
    viewport: { width: 1440, height: 1000 },
  },
});
