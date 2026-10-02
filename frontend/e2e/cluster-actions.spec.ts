import { expect, test } from "@playwright/test";
import { createTranslator } from "next-intl";
import german from "../messages/de.json";
import japanese from "../messages/ja.json";

const listStory = "screens-clusters-list-clusterslist";
const detailStory = "screens-clusters-list-clusterdetail";
const germanConnection = createTranslator({
  locale: "de",
  messages: german,
  namespace: "clusterConnection",
});
const germanList = createTranslator({ locale: "de", messages: german, namespace: "clusters.list" });

test("German cluster row keeps ordinary and full-preflight actions distinct at 768px", async ({
  page,
}) => {
  await page.goto(`/iframe.html?id=${listStory}--german-width-768&viewMode=story`);
  await expect(page.getByPlaceholder(german.clusters.list.searchPlaceholder)).toBeVisible();
  const row = page.getByRole("row").filter({ hasText: "Production US West" });
  await expect(
    row.getByText(german.clusters.chrome.lifecycle.managed, { exact: true })
  ).toBeVisible();
  await expect(row.getByRole("link")).toHaveAttribute("href", "/clusters/prd-us-west-2");
  await row.getByText(german.clusterConnection.connected, { exact: true }).hover();
  await expect(page.getByRole("tooltip")).toHaveText(
    germanList("heartbeatLast", { age: germanConnection("secondsAgo", { count: 12 }) })
  );
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);

  await row
    .getByRole("button", {
      name: german.shared.list.rowActions.replace("{label}", german.clusters.chrome.clusters),
    })
    .click();
  await expect(
    page.getByRole("menuitem", { name: german.clusters.actions.refresh, exact: true })
  ).toBeEnabled();
  await page
    .getByRole("menuitem", { name: german.clusters.actions.fullPreflight, exact: true })
    .click();
  await expect(page.getByRole("menu")).toHaveCount(0);
  await expect(page.getByRole("link", { name: german.clusters.list.register })).toHaveAttribute(
    "href",
    "/clusters/new"
  );
});

test("German cluster detail translates failure and capability controls while retaining raw diagnostics", async ({
  page,
}) => {
  await page.goto(`/iframe.html?id=${detailStory}--german-width-768&viewMode=story`);
  await expect(page.getByText(german.clusters.detail.failureTitle, { exact: true })).toBeVisible();
  await expect(page.getByText(german.clusters.detail.metricsServer, { exact: true })).toBeVisible();
  await expect(
    page.getByText(german.clusters.detail.stats.appsBound, { exact: true })
  ).toBeVisible();
  await expect(page.getByText(german.clusters.detail.stats.memory, { exact: true })).toBeVisible();

  await expect(page.getByText("exec_plugin", { exact: true })).toBeVisible();
  await expect(page.locator("#storybook-root pre")).toContainText(
    "Management workflow failed: the platform could not reach arn:aws:eks:"
  );
  await expect(
    page.getByRole("button", { name: german.clusters.actions.retry, exact: true })
  ).toBeEnabled();
  const help = page.getByText(german.clusters.detail.actionRequired, { exact: true }).locator("..");
  await expect(help.getByRole("link")).toHaveAttribute(
    "href",
    /\/clusters\/prd-us-west-2-tenant-shared-workloads-with-a-deliberately-long-slug\/settings$/
  );
  await page.getByRole("button", { name: german.clusters.detail.menuLabel }).click();
  await expect(
    page.getByRole("menuitem", { name: german.clusters.chrome.tabs.settings })
  ).toHaveAttribute("href", /\/settings$/);
});

test("Japanese cluster cards and registered detail retain target routes and technical identifiers", async ({
  page,
}) => {
  await page.goto(`/iframe.html?id=${listStory}--japanese-cards&viewMode=story`);
  await expect(page.getByRole("link", { name: japanese.clusters.list.register })).toBeVisible();
  await expect(page.getByText(japanese.clusterConnection.connected, { exact: true })).toBeVisible();
  const card = page.getByRole("link").filter({ hasText: "Production US West" });
  await expect(card).toHaveAttribute("href", "/clusters/prd-us-west-2");
  await expect(card).toContainText("us-west-2");
  await page.goto(`/iframe.html?id=${detailStory}--japanese-registered&viewMode=story`);
  await expect(page.getByText(japanese.clusters.detail.notManaged, { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: japanese.clusters.actions.bring, exact: true })
  ).toBeEnabled();
  await expect(page.getByRole("definition").filter({ hasText: /^Kubernetes$/ })).toBeVisible();
  const help = page
    .getByText(japanese.clusters.detail.actionRequired, { exact: true })
    .locator("..");
  await expect(help.getByRole("link")).toHaveAttribute("href", "/clusters/onprem-lab/settings");
});
