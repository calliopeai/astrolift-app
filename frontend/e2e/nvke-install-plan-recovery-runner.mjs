import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
const messages = JSON.parse(readFileSync(new URL("../messages/en.json", import.meta.url), "utf8"));
import { chromium } from "@playwright/test";
const base = process.env.NVKE_STORYBOOK_URL ?? "http://127.0.0.1:16407";
const planId = "123e4567-e89b-12d3-a456-426614174097";
const requestText = ` { "format": 1, "planId": "${planId}", "request": {"fixture": "private-native-text"}, "maximumInputBytes": "4096" }\n`;
const browser = await chromium.launch({ headless: true });
try {
  for (const scenario of ["lost-before-retention", "lost-after-retention"]) {
    const context = await browser.newContext();
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(
      `${base}/iframe.html?id=screens-nvke-controlled-plan-recovery--${scenario}&viewMode=story`,
      { timeout: 120000 }
    );
    const prepare = page.getByRole("button", { name: "Prepare plan", exact: true });
    await prepare.waitFor({ timeout: 120000 });
    await prepare.isEnabled();
    await page.getByLabel("Plan UUID").fill(planId);
    await page.getByLabel("Private plan request JSON").fill(requestText);
    await prepare.click();
    await page.waitForFunction(() => window.__NVKE_PLAN_FIXTURE_CALLS__?.length === 1);
    await page.getByRole("button", { name: "Review saved plan", exact: true }).click();
    if (scenario === "lost-before-retention") {
      const text = page.getByLabel("Private plan request JSON");
      await text.waitFor();
      await text.fill(requestText + " ");
      await prepare.click();
      await page
        .getByText(messages.nvke.recoveryMismatch, { exact: true })
        .waitFor({ timeout: 30000 });
      assert.equal(
        await page.evaluate(
          () =>
            window.__NVKE_PLAN_FIXTURE_CALLS__?.filter((c) => c.name === "PrepareNvkeInstallPlan")
              .length
        ),
        1
      );
      await text.fill(requestText);
      await prepare.click();
    }
    await page.getByText("postgresql", { exact: true }).waitFor({ timeout: 30000 });
    const calls = await page.evaluate(() => window.__NVKE_PLAN_FIXTURE_CALLS__);
    const preparations = calls.filter((c) => c.name === "PrepareNvkeInstallPlan");
    assert.equal(preparations.length, scenario === "lost-before-retention" ? 2 : 1);
    assert(preparations.every((c) => c.journalBeforeCall === true));
    assert.equal(new Set(preparations.map((c) => c.inputSha256)).size, 1);
    assert(
      calls.filter((c) => c.name !== "PrepareNvkeInstallPlan").every((c) => !c.hasPrivateText)
    );
    const storage = await page.evaluate(() => Object.values(localStorage).join("\n"));
    assert(!storage.includes("private-native-text"));
    assert(!storage.includes("requestText"));
    assert.deepEqual(errors, []);
    console.log(
      JSON.stringify({ scenario, preparations: preparations.length, pageErrors: errors })
    );
    await context.close();
  }
} finally {
  await browser.close();
}
