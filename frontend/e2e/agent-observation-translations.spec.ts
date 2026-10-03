import { expect, test } from "@playwright/test";

test("French ended session preserves literal ID and theatre recovery routes", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-agents-runs-agentvncpopout--french-session-ended&viewMode=story"
  );
  await expect(
    page.getByRole("heading", { name: "Session de l’agent en direct", exact: true })
  ).toBeVisible();
  await expect(
    page.getByText("Cet agent ne s’exécute plus ; sa session en direct est fermée.", {
      exact: true,
    })
  ).toBeVisible();
  await expect(
    page.getByText("7f3c2a10-4d5e-4f60-9a1b-2c3d4e5f6a7b", { exact: true })
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "Retour au théâtre", exact: true })).toHaveAttribute(
    "href",
    "/agents?tab=theatre"
  );
  await expect(
    page.getByRole("region", { name: "Session de l’agent en direct", exact: true })
  ).toHaveCount(0);
});

test("Japanese unavailable VNC session cannot present an active framebuffer", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-agents-runs-agentvncpopout--japanese-unavailable&viewMode=story"
  );
  await expect(
    page.getByText("このエージェントでは VNC 対応のセッションが実行されていません。", {
      exact: true,
    })
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "シアターに戻る", exact: true })).toHaveAttribute(
    "href",
    "/agents?tab=theatre"
  );
});

test("French no-relay viewer exposes localized controls and honest connection failure", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=patterns-observability-vncviewer--french-no-relay&viewMode=story"
  );
  await expect(
    page.getByRole("button", { name: "Adapter la session à la fenêtre", exact: true })
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Afficher la session en taille réelle", exact: true })
  ).toBeDisabled();
  await expect(page.getByText("Erreur de connexion.", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Session de l’agent en direct", exact: true })
  ).toBeVisible();
});

test("French log paging labels and shared controls preserve raw log output and run route", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-detail-agenttasklogs--french-earlier&viewMode=story"
  );
  for (const name of [
    "Charger les précédents",
    "Actualiser le journal",
    "Suivre",
    "Télécharger le journal",
  ])
    await expect(page.getByRole("button", { name, exact: true })).toBeVisible();
  await expect(page.getByText("Journaux du pod en direct", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Ouvrir l’exécution", exact: true })).toHaveAttribute(
    "href",
    "/agents/runs/3f9c2a1e-7b4d-4e8a-9c61-2d5f0b8e4a17"
  );
  await expect(
    page.getByText("agent starting (model=claude-sonnet, tools=4)", { exact: true })
  ).toBeVisible();
  await expect(page.getByText("INF", { exact: true }).first()).toBeVisible();
});

test("Japanese spawn failure retains server diagnostics beside translated guidance", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-detail-agenttasklogs--japanese-spawn-failed&viewMode=story"
  );
  await expect(page.getByText("Pod の起動前に実行が失敗しました", { exact: true })).toBeVisible();
  await expect(
    page.getByText(
      'spawn failed: pods "agent-research-7f9c" is forbidden: exceeded quota: agents-quota, requested: limits.memory=4Gi, used: limits.memory=14Gi, limited: limits.memory=16Gi',
      { exact: true }
    )
  ).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
});

test("unknown wire log level stays literal and neutral in a localized log pane", async ({
  page,
}) => {
  await page.goto("/iframe.html?id=run-logview--japanese-unknown-wire-level&viewMode=story");
  await expect(page.getByText("RAW_FUTURE_LEVEL_BODY", { exact: true })).toBeVisible();
  await expect(page.getByText("future_level_v2", { exact: true })).toHaveAttribute(
    "title",
    "future_level_v2"
  );
  await expect(page.locator("[data-level=other]")).toBeVisible();
  await expect(page.getByRole("button", { name: "追従", exact: true })).toBeVisible();
});
