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

test("cluster Health empty reports do not diagnose reachability or success", async ({ page }) => {
  await page.goto("/iframe.html?id=screens-clusters-status-clusterhealth--empty&viewMode=story");
  await expect(
    page.getByText(
      "No pod phases or events were returned. This does not confirm apiserver reachability or cluster health."
    )
  ).toBeVisible();
  await expect(
    page.getByText(
      "No deployments were returned. This does not confirm apiserver reachability or the absence of workloads."
    )
  ).toBeVisible();
  await expect(page.getByText(/couldn't reach the apiserver|green Live health/)).toHaveCount(0);
});

test("cluster Health preserves cached reports and exposes independent retries", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-clusters-status-clusterhealth--cached-error&viewMode=story"
  );
  const live = page.getByRole("region", { name: "Live health" });
  const workloads = page.getByRole("region", { name: "Workload health" });
  await expect(live.getByRole("alert")).toContainText("current health is unconfirmed");
  await expect(workloads.getByRole("alert")).toContainText("current health is unconfirmed");
  await expect(workloads.getByRole("table")).toBeVisible();
  await expect(live.getByRole("button", { name: "Retry", exact: true })).toBeEnabled();
  await expect(workloads.getByRole("button", { name: "Retry", exact: true })).toBeEnabled();
});

test("cluster Health malformed observations remain neutral and dates remain unknown", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-clusters-status-clusterhealth--unknown-observations&viewMode=story"
  );
  const live = page.getByRole("region", { name: "Live health" });
  const workloads = page.getByRole("region", { name: "Workload health" });
  await expect(live.getByTitle("FuturePhase")).toContainText("Unknown");
  await expect(live.getByTitle("FuturePhase")).toHaveClass(/text-muted-foreground/);
  await expect(workloads.getByText("0 / 0", { exact: true })).toHaveClass(/text-muted-foreground/);
  await expect(workloads.getByText("Unknown", { exact: true })).toHaveCount(3);
  await expect(page.getByText(/Invalid Date|NaN/)).toHaveCount(0);
});
