import { defineConfig, devices } from "@playwright/test";

/**
 * The layout guardrail (spec 44 §8): the long-string and 768px stories of
 * every dialog, sheet, table and list, rendered in a real browser from the
 * static Storybook build. Rules, not pixels, so macOS and CI fonts agree.
 *
 * Build the catalog first (`npm run build-storybook`); STORYBOOK_DIR points
 * at another build output when storybook-static is not the one to test.
 */
const PORT = Number(process.env.LAYOUT_PORT ?? 6107);
const STORYBOOK_DIR = process.env.STORYBOOK_DIR ?? "storybook-static";

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: process.env.CI ? 4 : undefined,
  reporter: process.env.CI ? [["list"], ["github"]] : "list",
  timeout: 60_000,
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    ...devices["Desktop Chrome"],
    // 768px of content (spec 44 §6), the width every Width768 and At768
    // story draws its own frame at. The shared decorator pads each story by
    // 24px a side, the app's page gutter that the tab rows bleed into with
    // -mx-6, so the window is 768 + 2 x 24 = 816px wide.
    viewport: { width: 816, height: 900 },
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: {
    command: `node e2e/serve-static.mjs ${STORYBOOK_DIR} ${PORT}`,
    url: `http://127.0.0.1:${PORT}/index.json`,
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});
