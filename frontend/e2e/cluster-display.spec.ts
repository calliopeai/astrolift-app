import { expect, test } from "@playwright/test";

test("managed cluster menu exposes full preflight separately from ordinary refresh", async ({
  page,
}) => {
  await page.goto("/iframe.html?id=screens-clusters-list-clusterslist--full&viewMode=story");
  await page
    .getByRole("row")
    .filter({ hasText: "Production US West" })
    .getByRole("button", { name: "Clusters: row actions" })
    .click();
  await expect(page.getByRole("menuitem", { name: "Refresh setup", exact: true })).toBeVisible();
  const full = page.getByRole("menuitem", {
    name: "Refresh setup with full preflight",
    exact: true,
  });
  await expect(full).toBeEnabled();
  await full.click();
  await expect(page.getByRole("menu")).toHaveCount(0);
});

test("charts from separate clusters retain their own gradient paint servers", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-administration-insights-clustermetricspanels--multiple-clusters&viewMode=story"
  );
  const gradients = page.locator("linearGradient[id]");
  await expect(gradients).toHaveCount(12);
  const painted = await gradients.evaluateAll((elements) =>
    elements.map((gradient) => ({
      id: gradient.id,
      localReference: !!gradient.closest("svg")?.querySelector(`[fill="url(#${gradient.id})"]`),
    }))
  );
  expect(new Set(painted.map(({ id }) => id)).size).toBe(painted.length);
  expect(painted.every(({ localReference }) => localReference)).toBe(true);
});
