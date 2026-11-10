import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chromium } from "@playwright/test";
const base = process.env.NVKE_STORYBOOK_URL ?? "http://127.0.0.1:54367";
const browser = await chromium.launch({ headless: true });
const errors = [];
let cases = 0;
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  page.on("pageerror", () => errors.push("page-error"));
  for (const name of ["bound", "required", "unloaded", "pending", "read-only", "stale"]) {
    await page.goto(
      `${base}/iframe.html?id=screens-nvke-install-registry-image--workload-${name}&viewMode=story&globals=mode:light`,
      { timeout: 120000 }
    );
    const heading = page.getByRole("heading", { name: "Workload registry image", exact: true });
    await heading.waitFor({ timeout: 120000 });
    const section = heading.locator("xpath=ancestor::section[1]");
    const read = section.getByRole("button", { name: "Review saved image", exact: true });
    assert.equal(await read.isEnabled(), ["bound", "unloaded"].includes(name));
    const root = section.getByText("sha256:" + "4".repeat(64), { exact: true });
    if (["bound", "pending"].includes(name)) await root.waitFor();
    else assert.equal(await root.count(), 0);
    if (["required", "read-only", "stale"].includes(name))
      assert.equal(
        await section.getByText("c098fc8d-3f48-45fd-b9b0-7d670261e9ef", { exact: true }).count(),
        0
      );
    if (name === "bound") {
      await section.getByText("c098fc8d-3f48-45fd-b9b0-7d670261e9ef", { exact: true }).waitFor();
      await page.screenshot({
        path: "/tmp/astrolift-nvke-workload-image-full.png",
        fullPage: true,
      });
      await page.setViewportSize({ width: 768, height: 1100 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 768);
      await page.screenshot({ path: "/tmp/astrolift-nvke-workload-image-768.png", fullPage: true });
      await page.setViewportSize({ width: 1440, height: 1100 });
    }
    assert.equal(await page.getByRole("button", { name: /execute|continue|resume/i }).count(), 0);
    cases++;
  }
  for (const [story, locale] of Object.entries({
    german: "de",
    spanish: "es",
    french: "fr",
    portuguese: "pt-BR",
    japanese: "ja",
    korean: "ko",
    chinese: "zh-Hans",
  })) {
    const copy = JSON.parse(
      readFileSync(new URL(`../messages/${locale}.json`, import.meta.url), "utf8")
    ).nvke.installImage;
    await page.goto(
      `${base}/iframe.html?id=screens-nvke-install-registry-image--workload-${story}&viewMode=story&globals=mode:light`,
      { timeout: 120000 }
    );
    await page
      .getByRole("heading", { name: copy.workloadTitle, exact: true })
      .waitFor({ timeout: 120000 });
    await page.getByText(copy.workloadDigest, { exact: true }).waitFor();
    cases++;
  }
  await page.goto(
    `${base}/iframe.html?id=screens-nvke-install-registry-image--workload-bound&viewMode=story&globals=mode:dark`,
    { timeout: 120000 }
  );
  await page
    .getByRole("heading", { name: "Workload registry image", exact: true })
    .waitFor({ timeout: 120000 });
  await page.waitForFunction(() => document.documentElement.classList.contains("dark"));
  await page.screenshot({ path: "/tmp/astrolift-nvke-workload-image-dark.png", fullPage: true });
  await page.goto(
    `${base}/iframe.html?id=screens-nvke-operation--install-plan-workload-image&viewMode=story&globals=mode:light`,
    { timeout: 120000 }
  );
  await page
    .getByRole("heading", { name: "Workload registry image", exact: true })
    .waitFor({ timeout: 120000 });
  await page.screenshot({
    path: "/tmp/astrolift-nvke-workload-image-operation.png",
    fullPage: true,
  });
  cases++;
  assert.deepEqual(errors, []);
  console.log(
    JSON.stringify({
      passed: true,
      cases,
      locales: 8,
      widths: [1440, 768],
      themes: ["light", "dark"],
      pageErrors: errors,
    })
  );
} finally {
  await browser.close();
}
