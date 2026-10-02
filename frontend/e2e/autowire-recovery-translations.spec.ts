import { expect, test } from "@playwright/test";

test("French recovery retains literal diagnostics and fits the app gutter", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-apps-overview-autowirestatusbanner--french-recovery&viewMode=story"
  );
  await expect(page.getByText("Configuration automatique incomplète")).toBeVisible();
  await expect(page.getByRole("button", { name: "Réessayer la configuration" })).toBeEnabled();
  await expect(page.getByText("rate_limited")).toBeVisible();
  await expect(page.getByText("RAW_PROVIDER_DIAGNOSTIC")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
});
test("Japanese connection preserves its destination", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-apps-overview-autowirestatusbanner--japanese-connect&viewMode=story"
  );
  await expect(page.getByText("自動デプロイを接続", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "接続", exact: true })).toHaveAttribute(
    "href",
    "/settings/source-providers"
  );
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
});
test("an in-flight repair disables retry and fully wired apps have no warning", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-apps-overview-autowirestatusbanner--loading&viewMode=story"
  );
  await expect(page.getByRole("button", { name: "Retry autowire" })).toBeDisabled();
  await page.goto(
    "/iframe.html?id=screens-apps-overview-autowirestatusbanner--empty&viewMode=story"
  );
  await expect(page.getByTestId("slot")).toBeEmpty();
});
