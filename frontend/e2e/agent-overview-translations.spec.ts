import { expect, test } from "@playwright/test";

test("French overview labels preserve image and run targets", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-agents-detail-agentoverview--french-runtime&viewMode=story"
  );
  for (const text of [
    "Dernière exécution",
    "Environnement d’exécution",
    "Exécutions récentes",
    "Dans la flotte",
  ])
    await expect(page.getByText(text, { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Ouvrir l’exécution", exact: true })).toHaveAttribute(
    "href",
    "/agents/runs/7f3c9a10-1111-4a2b-9c3d-000000000001"
  );
  await expect(page.getByRole("link", { name: "Configuration", exact: true })).toHaveAttribute(
    "href",
    "/agents/research-scout/configuration"
  );
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
});

test("Japanese unknown run status stays literal and malformed dates stay neutral", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-detail-agentoverview--japanese-unknown-run&viewMode=story"
  );
  await expect(page.getByText("future_status_v2", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("日付不明", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Invalid Date", { exact: true })).toHaveCount(0);
});

test("localized agent section navigation preserves exact section query and mounts only selected configuration", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-detail-agenttabsections--french-access&viewMode=story"
  );
  const nav = page.getByRole("navigation", { name: "Sections", exact: true });
  await expect(
    nav.getByRole("link", { name: "Jetons de déploiement", exact: true })
  ).toHaveAttribute("href", "/agents/literal-agent/access?section=tokens");
  await expect(
    nav.getByRole("link", { name: "Jetons de déploiement", exact: true })
  ).toHaveAttribute("aria-current", "page");
  await page.goto(
    "/iframe.html?id=screens-agents-detail-agentconfigurationtab--japanese-configuration&viewMode=story"
  );
  await expect(page.getByText("LITERAL_MODEL", { exact: true })).toBeVisible();
  await expect(page.getByText("LITERAL_BUILD", { exact: true })).toHaveCount(0);
  await expect(
    page.getByText(
      "エージェントがモデルへアクセスする方法と、実行をライブ表示できるかどうかです。",
      { exact: true }
    )
  ).toBeVisible();
});
