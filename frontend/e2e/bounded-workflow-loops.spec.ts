import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

const labels = (locale: string) =>
  JSON.parse(readFileSync(path.join("messages", `${locale}.json`), "utf8")).workflowBounds;

for (const locale of ["en", "es", "de", "fr", "ja", "ko", "pt-BR", "zh-Hans"]) {
  test(`bounded review controls retain target and budget in ${locale}`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/iframe.html?id=workflows-boundedstageoptions--translations&viewMode=story");
    const section = page.locator(`[data-locale="${locale}"]`);
    const t = labels(locale);
    await expect(section.getByLabel(t.rounds)).toHaveValue("5");
    await section.getByLabel(t.target).selectOption("assessment");
    await section.getByLabel(t.rounds).fill("3");
    await section.getByLabel(t.exhausted).selectOption("fail");
    await expect(section.getByLabel(t.target)).toHaveValue("assessment");
    await expect(section.getByLabel(t.rounds)).toHaveValue("3");
    await expect(section.getByLabel(t.exhausted)).toHaveValue("fail");
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
    ).toBe(true);
    expect(errors).toEqual([]);
  });
}

test("the timeline displays supplied round bounds and cause", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-workflows-detail-workflowrunscreen--looped&viewMode=story"
  );
  await expect(page.getByText("Round 2 of 5", { exact: true })).toBeVisible();
  await expect(
    page
      .getByRole("listitem")
      .filter({ has: page.getByText("Round 2 of 5", { exact: true }) })
      .getByText("stage_failed", { exact: true })
  ).toBeVisible();
});

test("the fan-out timeline exposes each branch start time", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-workflows-detail-workflowrunscreen--full&viewMode=story"
  );
  await expect(page.getByText(/^Started /)).toHaveCount(3);
});
