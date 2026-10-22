import { expect, test } from "@playwright/test";

async function story(page: import("@playwright/test").Page, name: string) {
  await page.goto(`/iframe.html?id=screens-projects-${name}&viewMode=story`);
}

test("metadata list retains paged total and unknown provider states", async ({ page }) => {
  await story(page, "managedresourcelist--paged");
  await expect(page.getByRole("button", { name: "shared-cache", exact: true })).toBeVisible();
  await expect(page.getByText(/251/).first()).toBeVisible();
  await story(page, "managedresourcelist--unknown-status");
  await expect(page.getByText("provider_phase_v2", { exact: true })).toBeVisible();
});

test("changed context requires another exact review before writes", async ({ page }) => {
  await story(page, "managedresourcedetail--changed-context");
  await expect(page.getByRole("button", { name: "Review again" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reprovision", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Preview cost", exact: true })).toHaveCount(0);
});

test("read-only resource detail has no write or cost controls", async ({ page }) => {
  await story(page, "managedresourcedetail--read-only");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("button", { name: "Reprovision", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Detach", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Preview cost", exact: true })).toHaveCount(0);
});

test("long exact identifiers remain within the 768px sheet", async ({ page }) => {
  await story(page, "managedresourcedetail--long-strings");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  expect(
    await dialog.evaluate((element) => element.scrollWidth - element.clientWidth)
  ).toBeLessThanOrEqual(1);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    )
  ).toBeLessThanOrEqual(1);
});

test("refused metrics context shows unavailable without old samples or zero charts", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=patterns-observability-managedservicemetricspanel--refused-context&viewMode=story"
  );
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.locator(".recharts-wrapper")).toHaveCount(0);
  await expect(page.getByRole("button")).toHaveCount(0);
});
