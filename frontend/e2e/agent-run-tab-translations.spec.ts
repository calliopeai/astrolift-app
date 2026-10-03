import { expect, test } from "@playwright/test";
import fr from "../messages/fr.json";
import ja from "../messages/ja.json";

test("French catalog run table keeps exact run links and headless watch target", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-detail-agentrunscreen--french-runs&viewMode=story"
  );
  await expect(
    page.getByRole("columnheader", { name: fr.agentRunTab.status, exact: true })
  ).toBeVisible();
  await expect(page.getByText(fr.agentRunTab.runningNow, { exact: true })).toHaveCount(2);
  const id = "a1b2c3d4-0000-4000-8000-000000000002";
  await expect(page.getByRole("link").filter({ hasText: id }).first()).toHaveAttribute(
    "href",
    `/agents/runs/${id}`
  );
  await page
    .getByRole("button", {
      name: fr.shared.list.rowActions.replace("{label}", fr.agentRunTab.runs),
      exact: true,
    })
    .nth(1)
    .click();
  await page.getByRole("menuitem", { name: fr.agentRunTab.watchLogs, exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("heading", { name: fr.agentRunTab.logsTitle, exact: true })
  ).toBeVisible();
  await expect(dialog.getByText(id, { exact: true })).toBeVisible();
  await expect(
    dialog.getByRole("log", { name: fr.agentRunTab.terminal.label, exact: true })
  ).toBeVisible();
});
test("Japanese catalog Mine view discloses unavailable initiator filtering", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-agents-detail-agentrunscreen--japanese-mine&viewMode=story"
  );
  for (const key of ["mineNote"] as const)
    await expect(page.getByText(ja.agentRunTab[key], { exact: true })).toBeVisible();
  await expect(
    page.getByText(
      ja.shared.list.emptyView
        .replace("{label}", ja.agentRunTab.runs)
        .replace("{view}", ja.agentRunTab.views.mine),
      { exact: true }
    )
  ).toBeVisible();
});
test("Japanese future wire status and malformed timestamp remain literal and neutral", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-detail-agentrunscreen--japanese-unknown-run&viewMode=story"
  );
  await expect(page.getByText("future_status_v2", { exact: true })).toHaveAttribute(
    "title",
    "future_status_v2"
  );
  await expect(page.getByText(ja.agentRunTab.unknownDate, { exact: true })).toHaveAttribute(
    "title",
    "malformed"
  );
  await page
    .getByRole("button", {
      name: ja.shared.list.rowActions.replace("{label}", ja.agentRunTab.runs),
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("menuitem", { name: ja.agentRunTab.openRun, exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole("menuitem", { name: ja.agentRunTab.watchLive, exact: true })
  ).toHaveCount(0);
  await expect(
    page.getByRole("menuitem", { name: ja.agentRunTab.watchLogs, exact: true })
  ).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
});
test("French terminal waiting and failure prefixes keep raw diagnostic text", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=patterns-observability-livelogterminal--french-waiting&viewMode=story"
  );
  await expect(page.getByText(fr.agentRunTab.terminal.waiting, { exact: true })).toBeVisible();
  await page.goto(
    "/iframe.html?id=patterns-observability-livelogterminal--french-read-error&viewMode=story"
  );
  await expect(page.getByRole("status")).toHaveText(
    `${fr.agentRunTab.terminal.readFailed} RAW_LOG_DIAGNOSTIC`
  );
});
test("Japanese empty terminal does not claim retained historical output", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=patterns-observability-livelogterminal--japanese-unavailable&viewMode=story"
  );
  await expect(page.getByText(ja.agentRunTab.terminal.empty, { exact: true })).toBeVisible();
});
