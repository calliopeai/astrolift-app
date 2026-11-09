import assert from "node:assert/strict";
import { chromium } from "@playwright/test";

const base = process.env.NVKE_STORYBOOK_URL ?? "http://127.0.0.1:16408";
const artifact = "123e4567-e89b-12d3-a456-426614174096";
const digest = "e".repeat(64);
const cases = [
  "full",
  "unloaded",
  "parent-required",
  "loading",
  "pending",
  "lost-reply",
  "read-only",
  "other-reviewer",
  "stale-parent",
  "stale-source",
  "unverified",
  "empty",
  "original-preparation-recovery",
  "choose-another-artifact",
  "width-768",
];
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  for (const name of cases) {
    await page.goto(
      `${base}/iframe.html?id=screens-nvke-install-plan-charts--${name}&viewMode=story`,
      { timeout: 120000 }
    );
    await page.getByText("Plan chart artifacts", { exact: true }).waitFor({ timeout: 120000 });
    assert.equal(await page.getByRole("button", { name: /execute|continue|resume/i }).count(), 0);
    if (
      [
        "read-only",
        "parent-required",
        "stale-source",
        "unverified",
        "stale-parent",
        "loading",
        "lost-reply",
      ].includes(name)
    )
      assert.equal(await page.getByText(digest, { exact: true }).count(), 0);
    if (
      ["read-only", "parent-required", "stale-source", "unverified", "loading", "pending"].includes(
        name
      )
    )
      assert.equal(
        await page
          .getByRole("button", { name: "Review chart artifacts", exact: true })
          .isDisabled(),
        true
      );
    if (name === "full") {
      assert.equal(await page.getByText("9007199254740993", { exact: true }).count(), 1);
      assert.equal(await page.getByText("9223372036854775807", { exact: true }).count(), 1);
      assert.equal(
        await page
          .getByRole("button", { name: "Prepare chart artifacts", exact: true })
          .isDisabled(),
        true
      );
      await page.screenshot({ path: "/tmp/astrolift-nvke-chart-ui-full.png", fullPage: true });
      await page.setViewportSize({ width: 768, height: 1100 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 768);
      await page.screenshot({ path: "/tmp/astrolift-nvke-chart-ui-768.png", fullPage: true });
      await page.setViewportSize({ width: 1440, height: 1100 });
    }
    if (name === "unloaded") {
      await page.getByRole("button", { name: "New artifact UUID", exact: true }).click();
      const id = await page.getByLabel("Artifact UUID", { exact: true }).inputValue();
      assert.match(id, /^[0-9a-f-]{36}$/);
      await page.getByLabel("Archive byte limit", { exact: true }).fill("9007199254740993");
      await page.getByLabel("Expanded byte limit", { exact: true }).fill("9223372036854775807");
      await page.getByLabel("Timeout (seconds)", { exact: true }).fill("3600");
      await page
        .getByRole("checkbox", { name: "Allow HTTP chart repositories", exact: true })
        .check();
      await page.getByRole("button", { name: "Prepare chart artifacts", exact: true }).click();
      await page.getByRole("button", { name: "Prepare chart artifacts", exact: true }).waitFor();
      await page.waitForFunction(
        () => document.querySelector('input[id$="-artifact"]')?.disabled === true
      );
      assert.equal(await page.getByLabel("Artifact UUID", { exact: true }).inputValue(), id);
      assert.equal(await page.getByLabel("Archive byte limit", { exact: true }).count(), 0);
    }
    if (name === "lost-reply") {
      assert.equal(await page.getByLabel("Artifact UUID", { exact: true }).inputValue(), artifact);
      assert.equal(
        await page.getByRole("button", { name: "Review chart artifacts", exact: true }).isEnabled(),
        true
      );
    }
    if (name === "other-reviewer")
      assert.equal(
        await page
          .getByRole("button", { name: "Retry retained chart preparation", exact: true })
          .isDisabled(),
        true
      );
    if (name === "original-preparation-recovery") {
      await page.getByLabel("Archive byte limit", { exact: true }).fill("1024");
      await page.getByLabel("Expanded byte limit", { exact: true }).fill("10240");
      await page.getByRole("button", { name: "Prepare chart artifacts", exact: true }).click();
      await page.waitForFunction(
        () => document.querySelector('input[id$="-maximumBytes"]')?.value === ""
      );
    }
    console.log(JSON.stringify({ story: name }));
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ completed: cases.length, pageErrors: errors }));
} finally {
  await browser.close();
}
