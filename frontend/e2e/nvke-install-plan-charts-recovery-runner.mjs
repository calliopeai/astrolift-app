import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chromium } from "@playwright/test";
const copy = JSON.parse(readFileSync(new URL("../messages/en.json", import.meta.url), "utf8")).nvke
  .installCharts;
const base = process.env.NVKE_STORYBOOK_URL ?? "http://127.0.0.1:16409";
const artifactId = "123e4567-e89b-12d3-a456-426614174096";
const browser = await chromium.launch({ headless: true });
try {
  for (const scenario of ["lost-before-retention", "lost-after-retention"]) {
    const context = await browser.newContext();
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(
      `${base}/iframe.html?id=screens-nvke-controlled-chart-recovery--${scenario}&viewMode=story`,
      { timeout: 120000 }
    );
    const prepare = page.getByRole("button", { name: copy.prepare, exact: true });
    await prepare.waitFor({ timeout: 120000 });
    await page.getByLabel(copy.artifactId, { exact: true }).fill(artifactId);
    const fillOptions = async (timeout) => {
      await page.getByLabel(copy.archiveLimit, { exact: true }).fill("9007199254740993");
      await page.getByLabel(copy.expandedLimit, { exact: true }).fill("9223372036854775807");
      await page.getByLabel(copy.timeout, { exact: true }).fill(timeout);
    };
    await fillOptions("300");
    await prepare.click();
    await page.getByText(copy.unconfirmed, { exact: true }).waitFor();
    assert.equal(await page.getByLabel(copy.artifactId, { exact: true }).inputValue(), artifactId);
    assert.equal(await prepare.isDisabled(), true);
    assert.equal(await page.getByLabel(copy.archiveLimit, { exact: true }).count(), 0);
    await page.getByRole("button", { name: copy.review, exact: true }).click();
    if (scenario === "lost-before-retention") {
      await page.getByLabel(copy.archiveLimit, { exact: true }).waitFor();
      await fillOptions("301");
      await prepare.click();
      await page.getByText(copy.unconfirmed, { exact: true }).waitFor();
      assert.equal(
        await page.evaluate(
          () =>
            window.__NVKE_CHART_FIXTURE_CALLS__.filter(
              (c) => c.name === "PrepareNvkeInstallPlanCharts"
            ).length
        ),
        1
      );
      await fillOptions("300");
      await prepare.click();
    }
    await page.getByText("9007199254740993", { exact: true }).waitFor();
    const calls = await page.evaluate(() => window.__NVKE_CHART_FIXTURE_CALLS__);
    const preparations = calls.filter((c) => c.name === "PrepareNvkeInstallPlanCharts");
    assert.equal(preparations.length, scenario === "lost-before-retention" ? 2 : 1);
    assert(preparations.every((c) => c.journalBeforeCall === true));
    assert.equal(new Set(preparations.map((c) => c.inputSha256)).size, 1);
    assert(
      calls.filter((c) => c.name !== "PrepareNvkeInstallPlanCharts").every((c) => !c.hasPrivateText)
    );
    const storage = await page.evaluate(() => Object.values(localStorage).join("\n"));
    assert(!storage.includes("maximumBytes"));
    assert(!storage.includes("requestText"));
    assert.equal(await page.getByRole("button", { name: /execute|continue|resume/i }).count(), 0);
    assert.deepEqual(errors, []);
    console.log(
      JSON.stringify({ scenario, preparations: preparations.length, pageErrors: errors })
    );
    await context.close();
  }
} finally {
  await browser.close();
}
