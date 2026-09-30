import { expect, test, type Page } from "@playwright/test";

test.use({ viewport: { width: 1280, height: 900 } });

async function story(page: Page, name: string) {
  await page.goto(`/iframe.html?id=screens-apps-controls-controlssection--${name}&viewMode=story`);
  await expect(page.getByRole("heading", { name: "Primary environment" })).toBeVisible();
}

test("workload actions appear once for primary rather than once per selected environment", async ({
  page,
}) => {
  await story(page, "full");
  const restarts = page.getByRole("button", { name: "Rolling restart", exact: true });
  await expect(restarts).toHaveCount(2);
  await restarts.first().click();
  await expect(page.getByRole("alertdialog")).toContainText("Primary environment");
  await expect(page.getByRole("alertdialog")).not.toContainText("in prod");
});

test("denied primary actions keep reasons visible and cannot dispatch from desktop or mobile", async ({
  page,
}) => {
  await story(page, "restricted-workload");
  await expect(page.getByRole("button", { name: "Rolling restart", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Increase replicas" })).toBeDisabled();
  await expect(page.getByText(/Production policy requires reauthentication/)).toBeVisible();
  await page.setViewportSize({ width: 480, height: 900 });
  await page.getByRole("button", { name: "Workload actions for Web" }).click();
  await expect(page.getByRole("menuitem", { name: "Rolling restart" })).toBeDisabled();
  await expect(page.getByRole("menuitem", { name: "Increase replicas" })).toBeDisabled();
});

test("restart and scale use independent object decisions", async ({ page }) => {
  await story(page, "scale-only");
  await expect(page.getByRole("button", { name: "Rolling restart", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Increase replicas" }).click();
  await expect(page.getByRole("button", { name: "Apply", exact: true })).toBeEnabled();
});

test("denied inline scale exposes a reason without offering an active control", async ({
  page,
}) => {
  await page.goto("/iframe.html?id=screens-apps-workloads-scalepopover--restricted&viewMode=story");
  await expect(page.getByRole("button", { name: "Scale", exact: true })).toBeDisabled();
  await expect(page.getByText("No role binding grants this permission")).toBeVisible();
});
