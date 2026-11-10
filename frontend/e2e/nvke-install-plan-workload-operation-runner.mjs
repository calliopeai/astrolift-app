import assert from "node:assert/strict";
import { chromium } from "@playwright/test";
const base = process.env.NVKE_STORYBOOK_URL ?? "http://127.0.0.1:54367";
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  for (const name of [
    "install-plan-workload",
    "install-plan-workload-read-only",
    "install-plan-workload-pending",
  ]) {
    await page.goto(
      `${base}/iframe.html?id=screens-nvke-operation--${name}&viewMode=story&globals=mode:light`,
      { timeout: 120000 }
    );
    await page
      .getByRole("heading", { name: "Workload inventory", exact: true })
      .waitFor({ timeout: 120000 });
    const inspect = page.getByRole("button", { name: "Review saved inventory", exact: true });
    assert.equal(await inspect.isEnabled(), name === "install-plan-workload");
    assert.equal(
      await page.getByRole("button", { name: "Collect inventory", exact: true }).isDisabled(),
      true
    );
    console.log("composedCase", name);
    await page.screenshot({
      path: `/tmp/astrolift-nvke-workload-operation-${name}.png`,
      fullPage: true,
    });
    const panel = page
      .getByRole("heading", { name: "Workload inventory", exact: true })
      .locator("xpath=ancestor::section[1]");
    const root = panel.getByText("4".repeat(64), { exact: true });
    if (name === "install-plan-workload-read-only") assert.equal(await root.count(), 0);
    else await root.waitFor();
  }
  assert.deepEqual(errors, []);
  console.log(
    "Qualified three composed operation workload states with permission and pending guards"
  );
} finally {
  await browser.close();
}
