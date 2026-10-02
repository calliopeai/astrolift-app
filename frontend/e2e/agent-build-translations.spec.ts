import { expect, test } from "@playwright/test";
import fr from "../messages/fr.json";
import ja from "../messages/ja.json";

test("French connected Build presentation preserves source refs, manifest and skill summary routes", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-detail-agentbuildscreen--french-build&viewMode=story"
  );
  for (const key of ["source", "image", "manifest", "brief", "skillsTools"] as const)
    await expect(page.getByText(fr.agentBuild[key], { exact: true }).first()).toBeVisible();
  await expect(
    page.getByRole("link", { name: "calliopeai/sales-agents", exact: true })
  ).toHaveAttribute("href", "https://github.com/calliopeai/sales-agents");
  await expect(
    page.getByRole("link", { name: fr.agentBuild.openManifest, exact: true })
  ).toHaveAttribute("href", "/apps/sales-agents/workloads/bdr-outreach");
  await expect(page.getByText("job", { exact: true })).toBeVisible();
  await expect(page.getByText("astrolift.toml", { exact: true })).toBeVisible();
  await expect(
    page.getByText("ghcr.io/calliopeai/sales-agents/bdr-outreach:0.1.37", { exact: true })
  ).toBeVisible();
  await expect(page.getByText("2 outils", { exact: true })).toBeVisible();
  await expect(page.locator('a[href="/agents/bdr-outreach/skills"]')).toBeVisible();
  await expect(page.locator("pre").filter({ hasText: '"model": "claude-sonnet"' })).toBeVisible();
});
test("Japanese refused detail join shows recovery feedback without claiming no brief/skills", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-detail-agentbuildscreen--japanese-read-failed&viewMode=story"
  );
  await expect(page.getByText(ja.agentBuild.briefFailed, { exact: true })).toBeVisible();
  await expect(page.getByText(ja.agentBuild.skillsFailed, { exact: true })).toBeVisible();
  await expect(page.getByText(ja.agentBuild.noBrief, { exact: true })).toHaveCount(0);
  await expect(page.getByText(ja.agentBuild.noSkills, { exact: true })).toHaveCount(0);
  await expect(
    page.getByText("ghcr.io/calliopeai/sales-agents/bdr-outreach:0.1.37", { exact: true })
  ).toBeVisible();
});
test("Japanese unknown prototype adapter and malformed assembled date remain literal/neutral", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-detail-agentbuildscreen--japanese-unknown-adapter&viewMode=story"
  );
  await expect(page.getByText("__proto__", { exact: true })).toBeVisible();
  await expect(page.getByText(ja.agentBuild.unknownDate, { exact: true })).toHaveAttribute(
    "title",
    "RAW_INVALID_DATE"
  );
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
});
test("French empty Build preserves registry recovery links", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-agents-detail-agentbuildscreen--french-empty&viewMode=story"
  );
  for (const key of ["noContainer", "noBrief", "noSkills"] as const)
    await expect(page.getByText(fr.agentBuild[key], { exact: true })).toBeVisible();
  await expect(
    page.getByRole("link", { name: fr.agentBuild.skillRegistry, exact: true })
  ).toHaveAttribute("href", "/agents/skills");
  await expect(
    page.getByRole("link", { name: fr.agentBuild.toolRegistry, exact: true })
  ).toHaveAttribute("href", "/agents/tools");
});
