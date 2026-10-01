import { expect, test } from "@playwright/test";

test("agent replicas and zero concurrency are visible before a save", async ({ page }) => {
  await page.goto("/iframe.html?id=screens-agents-detail-agentcontrol--service&viewMode=story");
  await expect(page.getByRole("slider", { name: "Replicas" })).toHaveValue("7");
  await expect(page.getByText(/save to set/)).toHaveCount(0);
  await page.goto("/iframe.html?id=screens-agents-detail-agentcontrol--loop&viewMode=story");
  await expect(page.getByRole("spinbutton", { name: "Concurrency cap" })).toHaveValue("0");
});

test("ingress reconciliation shows busy progress while its request is pending", async ({
  page,
}) => {
  await page.goto("/iframe.html?id=screens-clusters-settings-ingressauth--saving&viewMode=story");
  const reconcile = page.getByRole("button", { name: "Reconcile cluster Ingresses", exact: true });
  await expect(reconcile).toBeDisabled();
  await expect(reconcile.locator(".animate-spin")).toBeVisible();
});

for (const target of ["agents", "apps"] as const) {
  test(`${target} shared repository picker fits a narrow viewport`, async ({ page }) => {
    await page.setViewportSize({ width: 480, height: 900 });
    await page.goto(
      `/iframe.html?id=screens-${target}-new-${target === "agents" ? "agentrepopickerstep" : "repopickerstep"}--long-strings&viewMode=story`
    );
    await expect(page.getByText("Source connection", { exact: true })).toBeVisible();
    await expect(page.getByRole("combobox").first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      480
    );
  });
}
