import { defineConfig } from "@playwright/test";

const port = Number(process.env.ROUTE_PORT ?? 6171);
const apiPort = Number(process.env.ROUTE_API_PORT ?? 6172);

export default defineConfig({
  testDir: "e2e-routes",
  outputDir: "test-results/routes",
  workers: 1,
  timeout: 600_000,
  retries: 0,
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    browserName: "chromium",
    viewport: { width: 1440, height: 1000 },
    timezoneId: "UTC",
    locale: "en-US",
    trace: { mode: "retain-on-failure", snapshots: false, screenshots: false, sources: true },
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: "node e2e-routes/api.mjs",
      url: `http://127.0.0.1:${apiPort}/health`,
      reuseExistingServer: false,
      stdout: "pipe",
    },
    {
      command: `npm run start -- --port ${port}`,
      url: `http://127.0.0.1:${port}/auth/login`,
      timeout: 120_000,
      env: {
        NEXT_PUBLIC_API_ORIGIN: `http://127.0.0.1:${apiPort}`,
        API_INTERNAL_ROOT: `http://127.0.0.1:${apiPort}`,
        TZ: "UTC",
      },
      reuseExistingServer: false,
    },
  ],
});
