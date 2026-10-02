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

test("imported Flowise controls retain source identity and fallback when changing cap", async ({
  page,
}) => {
  await page.goto("/iframe.html?id=workflows-boundedstageoptions--imported-flowise&viewMode=story");
  const t = labels("en");
  for (const key of ["target", "trigger", "exhausted"])
    await expect(page.getByLabel(t[key])).toBeDisabled();
  await page.getByLabel(t.rounds).fill("2");
  await expect(page.getByLabel(t.rounds)).toHaveValue("2");
  await expect(page.getByLabel(t.target)).toHaveValue("flow_humaninputagentflow_0");
  await expect(page.getByLabel(t.exhausted)).toHaveValue("continue");
  await expect(page.getByText('Source fallback: "Bounded import done"')).toBeVisible();
});

for (const locale of ["en", "es", "de", "fr", "ja", "ko", "pt-BR", "zh-Hans"]) {
  test(`imported source controls preserve target and completion policy in ${locale}`, async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(
      "/iframe.html?id=workflows-boundedstageoptions--imported-translations&viewMode=story"
    );
    const section = page.locator(`[data-locale="${locale}"]`);
    const t = labels(locale);
    await expect(section.getByText(t.importedHint)).toBeVisible();
    for (const key of ["target", "trigger", "exhausted"])
      await expect(section.getByLabel(t[key])).toBeDisabled();
    await section.getByLabel(t.rounds).fill("2");
    await expect(section.getByLabel(t.rounds)).toHaveValue("2");
    await expect(section.getByLabel(t.target)).toHaveValue("flow_humaninputagentflow_0");
    await expect(section.getByLabel(t.exhausted)).toHaveValue("continue");
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
    ).toBe(true);
    expect(errors).toEqual([]);
  });
}

for (const locale of ["en", "es", "de", "fr", "ja", "ko", "pt-BR", "zh-Hans"]) {
  test(`serial collection controls preserve fixed source inputs in ${locale}`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const messages = JSON.parse(
      readFileSync(path.join("messages", `${locale}.json`), "utf8")
    ).workflowCollections;
    await page.goto(
      "/iframe.html?id=workflows-collectionstageoptions--translations&viewMode=story"
    );
    const section = page.locator(`[data-locale="${locale}"]`);
    await expect(section.getByLabel(messages.bodyEnd)).toBeDisabled();
    await section.getByLabel(messages.cap).fill("4");
    await expect(section.getByLabel("iteration JSON")).toContainText('"max_items":4');
    await expect(section.getByLabel("iteration JSON")).toContainText('"text":"second"');
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
    ).toBe(true);
  });
}
test("serial items are visibly separate from parallel branches", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-workflows-detail-workflowrunscreen--serial-collection&viewMode=story"
  );
  await expect(page.getByText("Collection item 1", { exact: true })).toBeVisible();
  await expect(page.getByText("Collection item 2", { exact: true })).toBeVisible();
  await expect(page.getByText(/branches settled/)).toHaveCount(0);
});
