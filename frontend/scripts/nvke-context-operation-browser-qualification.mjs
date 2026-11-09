import { chromium } from "@playwright/test";
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
const output = process.env.NVKE_CONTEXT_OPERATION_OUTPUT;
assert(output, "Select an owned operation browser output directory");
const base = process.env.NVKE_CONTEXT_STORYBOOK ?? "http://127.0.0.1:16418";
assert(["127.0.0.1", "localhost", "[::1]"].includes(new URL(base).hostname));
await mkdir(output, { recursive: true, mode: 0o700 });
const browser = await chromium.launch({ headless: true });
const report = {
  scope: "operation-screen-storybook-permission-and-pending-wiring",
  cases: [],
  errors: [],
};
try {
  for (const [name, disabled] of [
    ["install-plan-context", false],
    ["install-plan-context-read-only", true],
    ["install-plan-context-pending", true],
  ]) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 960 } });
    page.on("pageerror", (e) => report.errors.push(e.message));
    await page.goto(base + "/iframe.html?id=screens-nvke-operation--" + name + "&viewMode=story", {
      waitUntil: "domcontentloaded",
      timeout: 120000,
    });
    await page
      .getByRole("heading", { name: "Render context", exact: true })
      .waitFor({ timeout: 120000 });
    const button = page.getByRole("button", { name: "Capture render context", exact: true });
    assert.equal(await button.isDisabled(), true);
    assert.equal(
      await page.getByRole("button", { name: "Review saved context", exact: true }).isDisabled(),
      disabled
    );
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const path = output + "/" + name + ".png";
    await page.screenshot({ path, fullPage: true });
    report.cases.push({ name, captureDisabled: true, reviewDisabled: disabled, screenshot: path });
    await page.close();
  }
  assert.deepEqual(report.errors, []);
  await writeFile(output + "/report.json", JSON.stringify(report, null, 2) + "\n", { mode: 0o600 });
  console.log("3 operation-screen browser cases passed");
} finally {
  await browser.close();
}
