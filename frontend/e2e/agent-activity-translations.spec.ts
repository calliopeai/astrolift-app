import { expect, test } from "@playwright/test";

test("French timeline folds calls with localized counts and preserves tool names", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-runs-agentrundetail--french-timeline&viewMode=story"
  );
  await expect(page.getByText("20 appels précédents", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Explorez les appels dans la carte des interactions.", { exact: true })
  ).toBeVisible();
  await expect(page.getByText("crm.lookup_account_59", { exact: true })).toBeVisible();
  await expect(page.getByText("En file d’attente", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
});

test("French map labels render with literal tool and endpoint names", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-agents-runs-agentinteractionmap--french-live&viewMode=story"
  );
  for (const label of ["API de contrôle", "Outils", "Validations", "Signaux", "En cours"])
    await expect(page.getByText(label, { exact: true })).toBeVisible();
  await expect(page.getByText("crm.search_accounts", { exact: true })).toBeVisible();
  await expect(page.getByText("POST /agents/tasks/heartbeat", { exact: true })).toBeVisible();
});

test("locale change rebuilds real map nodes while retaining unknown statuses and names", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-runs-agentinteractionmap--locale-switch&viewMode=story"
  );
  await expect(page.getByText("API de contrôle", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "日本語", exact: true }).click();
  await expect(page.getByText("制御 API", { exact: true })).toBeVisible();
  await expect(page.getByText("API de contrôle", { exact: true })).toHaveCount(0);
  await expect(page.getByText("future_status_v2", { exact: true })).toBeVisible();
  await expect(page.getByText("crm.search_accounts", { exact: true })).toBeVisible();
});

test("Japanese empty map describes unavailable observations without inventing calls", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-runs-agentinteractionmap--japanese-empty&viewMode=story"
  );
  await expect(
    page.getByText("インタラクションはまだ記録されていません", { exact: true })
  ).toBeVisible();
  await expect(
    page.getByText(
      "実行中の制御 API の呼び出し、ツールの呼び出し、承認ゲート、シグナルがここに表示されます。",
      { exact: true }
    )
  ).toBeVisible();
  await expect(page.locator(".react-flow__node")).toHaveCount(0);
});
