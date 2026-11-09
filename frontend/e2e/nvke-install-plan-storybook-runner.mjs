import assert from "node:assert/strict";
import { chromium } from "@playwright/test";

const base = process.env.NVKE_STORYBOOK_URL ?? "http://127.0.0.1:16407";
const browser = await chromium.launch({ headless: true });
const cases = [
  "full",
  "unloaded",
  "loading",
  "pending",
  "lost-reply",
  "read-only",
  "other-reviewer",
  "stale-source",
  "unverified",
  "page-failure",
  "paging",
  "empty-filtered",
  "long-strings",
  "width-768",
];
let completed = 0;
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  for (const name of cases) {
    await page.goto(`${base}/iframe.html?id=screens-nvke-install-plan--${name}&viewMode=story`, {
      timeout: 120000,
    });
    await page
      .getByText("Remaining installation plan", { exact: true })
      .waitFor({ timeout: 120000 });
    if (["read-only", "stale-source", "unverified", "loading"].includes(name)) {
      assert.equal(await page.getByText("postgresql", { exact: true }).count(), 0);
    }
    if (name === "full") {
      assert.equal(await page.getByRole("table").count(), 2);
      assert.equal(await page.getByRole("heading", { name: "Deployment items" }).count(), 1);
      assert.equal(
        await page.getByRole("searchbox", { name: "Search deployment items" }).count(),
        1
      );
      assert.equal(
        await page.getByRole("button", { name: "Prepare plan", exact: true }).isDisabled(),
        true
      );
      await page.screenshot({ path: "/tmp/astrolift-nvke-install-plan-full.png", fullPage: true });
      await page.setViewportSize({ width: 768, height: 1100 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 768);
      await page.screenshot({ path: "/tmp/astrolift-nvke-install-plan-768.png", fullPage: true });
      await page.setViewportSize({ width: 1440, height: 1100 });
    }
    if (name === "lost-reply") {
      assert.equal(await page.getByLabel("Private plan request JSON").count(), 0);
      assert.equal(
        await page.getByRole("button", { name: "Review saved plan", exact: true }).isEnabled(),
        true
      );
    }
    if (name === "other-reviewer") {
      assert.equal(
        await page
          .getByRole("button", { name: "Retry retained preparation", exact: true })
          .isDisabled(),
        true
      );
    }
    completed += 1;
    console.log(JSON.stringify({ story: name, completed }));
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ completed, pageErrors: errors }));
} finally {
  await browser.close();
}
