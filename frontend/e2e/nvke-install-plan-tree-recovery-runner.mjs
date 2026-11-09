import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chromium } from "@playwright/test";
const copy = JSON.parse(readFileSync(new URL("../messages/en.json", import.meta.url), "utf8")).nvke
  .installTree;
const base = process.env.NVKE_STORYBOOK_URL ?? "http://127.0.0.1:16412";
const artifactId = "87a76c80-3468-43aa-b2be-e511d587d312";
const browser = await chromium.launch({ headless: true });
try {
  for (const scenario of ["lost-before-retention", "lost-after-retention"]) {
    const context = await browser.newContext();
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(
      `${base}/iframe.html?id=screens-nvke-controlled-tree-recovery--${scenario}&viewMode=story`,
      { timeout: 120000 }
    );
    const prepare = page.getByRole("button", { name: copy.prepare, exact: true });
    await prepare.waitFor({ timeout: 120000 });
    await page.getByLabel(copy.artifactId, { exact: true }).fill(artifactId);
    const fillOptions = async (limit) => {
      await page.getByLabel(copy.treeLimit, { exact: true }).fill(limit);
    };
    await fillOptions("9007199254740994");
    await prepare.click();
    await page.getByText(copy.unconfirmed, { exact: true }).waitFor();
    assert.equal(await page.getByLabel(copy.artifactId, { exact: true }).inputValue(), artifactId);
    assert.equal(await prepare.isDisabled(), true);
    assert.equal(await page.getByLabel(copy.treeLimit, { exact: true }).count(), 0);
    await page.getByRole("button", { name: copy.review, exact: true }).click();
    if (scenario === "lost-before-retention") {
      await page.getByLabel(copy.treeLimit, { exact: true }).waitFor();
      await fillOptions("9007199254740995");
      await prepare.click();
      await page.getByText(copy.unconfirmed, { exact: true }).waitFor();
      assert.equal(
        await page.evaluate(
          () =>
            window.__NVKE_TREE_FIXTURE_CALLS__.filter(
              (c) => c.name === "PrepareNvkeInstallPlanTree"
            ).length
        ),
        1
      );
      await fillOptions("9007199254740994");
      await prepare.click();
    }
    await page.getByText("9007199254740994", { exact: true }).waitFor();
    const calls = await page.evaluate(() => window.__NVKE_TREE_FIXTURE_CALLS__);
    const preparations = calls.filter((c) => c.name === "PrepareNvkeInstallPlanTree");
    assert.equal(preparations.length, scenario === "lost-before-retention" ? 2 : 1);
    assert(preparations.every((c) => c.journalBeforeCall === true));
    assert.equal(new Set(preparations.map((c) => c.inputSha256)).size, 1);
    assert(
      calls.filter((c) => c.name !== "PrepareNvkeInstallPlanTree").every((c) => !c.hasPrivateText)
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
