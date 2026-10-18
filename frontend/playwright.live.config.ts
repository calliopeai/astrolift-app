import { resolve, sep } from "node:path";
import { defineConfig } from "@playwright/test";

// The read-only signed-in smoke against a live install (#1815, #2271); see
// docs/operators/cognito-client-cleanup.md. No webServer: it drives the URL
// in LIVE_BASE_URL with the session saved by `npm run smoke:live:login`.
// Never in CI.
const baseURL = process.env.LIVE_BASE_URL;
const storageState = process.env.LIVE_STORAGE_STATE;
if (!baseURL || !storageState)
  throw new Error("Set LIVE_BASE_URL and LIVE_STORAGE_STATE (a path outside the repository).");
const repo = resolve(__dirname, "..");
if ((resolve(storageState) + sep).startsWith(repo + sep))
  throw new Error("LIVE_STORAGE_STATE holds session cookies; keep it outside the repository.");

export default defineConfig({
  testDir: "e2e-live",
  outputDir: "test-results/live/artifacts",
  reporter: [["list"], ["./e2e-live/report.ts"]],
  workers: 1,
  retries: 0,
  timeout: 120_000,
  use: {
    baseURL,
    browserName: "chromium",
    viewport: { width: 1440, height: 1000 },
    locale: "en-US",
    launchOptions: { args: ["--use-mock-keychain", "--password-store=basic"] },
    // Traces and videos would carry the session; the report is redacted instead.
    trace: "off",
    video: "off",
    screenshot: "off",
  },
  // `npm run smoke:live:login` (LIVE_LOGIN=1) opens a headed browser to sign
  // in and save the session; every other run is the headless smoke.
  projects: process.env.LIVE_LOGIN
    ? [{ name: "login", testMatch: "login.setup.ts", timeout: 900_000, use: { headless: false } }]
    : [{ name: "smoke", testIgnore: "login.setup.ts", use: { headless: true, storageState } }],
});
