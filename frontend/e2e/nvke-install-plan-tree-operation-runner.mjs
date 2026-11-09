import assert from "node:assert/strict";
import { chromium } from "@playwright/test";
const base = process.env.NVKE_STORYBOOK_URL ?? "http://127.0.0.1:16413";
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  for (const name of [
    "install-plan-tree",
    "install-plan-tree-read-only",
    "install-plan-tree-pending",
  ]) {
    await page.goto(`${base}/iframe.html?id=screens-nvke-operation--${name}&viewMode=story`, {
      timeout: 120000,
    });
    await page
      .getByRole("heading", { name: "Plan execution tree", exact: true })
      .waitFor({ timeout: 120000 });
    const review = page.getByRole("button", { name: "Review execution tree", exact: true });
    assert.equal(await review.isDisabled(), name !== "install-plan-tree");
    if (name === "install-plan-tree-read-only")
      assert.equal(await page.getByText("d".repeat(64), { exact: true }).count(), 0);
    if (name === "install-plan-tree") {
      assert.equal(await page.getByText("9007199254740994", { exact: true }).count(), 1);
      await page.screenshot({
        path: "/tmp/astrolift-nvke-tree-operation-full.png",
        fullPage: true,
      });
      await page.setViewportSize({ width: 768, height: 1100 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 768);
      await page.screenshot({ path: "/tmp/astrolift-nvke-tree-operation-768.png", fullPage: true });
      await page.setViewportSize({ width: 1440, height: 1100 });
    }
    console.log(JSON.stringify({ story: name }));
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ completed: 3, pageErrors: errors }));
} finally {
  await browser.close();
}
