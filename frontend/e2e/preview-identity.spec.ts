import { expect, test } from "@playwright/test";

test("basic preview detail leaves resource and cost reads explicit", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-previews-previewdetail--runtime-not-requested&viewMode=story"
  );
  await expect(page.getByRole("heading", { name: "PR #412" })).toBeVisible();
  await expect(page.getByText("review-target · Available")).toBeVisible();
  await expect(page.getByRole("button", { name: "Load resources and cost" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Load logs" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Load deployments" })).toBeEnabled();
  await expect(page.getByText("$0.00", { exact: true })).toHaveCount(0);
});

test("retired binding retains metadata without opening reused hostname or log fallback", async ({
  page,
}) => {
  await page.goto("/iframe.html?id=screens-previews-previewdetail--retired-binding&viewMode=story");
  await expect(page.getByRole("heading", { name: "PR #412" })).toBeVisible();
  await expect(page.getByText("review-target · Retired")).toBeVisible();
  await expect(page.locator('a[href="https://pr-412.checkout-api.preview.acme.dev"]')).toHaveCount(
    0
  );
  await expect(page.locator('a[href$="/logs"]')).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Load logs" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Load deployments" })).toBeDisabled();
});

test("exact loaded logs fit a narrow real browser", async ({ page }) => {
  await page.setViewportSize({ width: 480, height: 900 });
  await page.goto(
    "/iframe.html?id=screens-previews-previewdetail--exact-logs-loaded&viewMode=story"
  );
  await expect(page.getByText("Preview application ready")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false
  );
});
