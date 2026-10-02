import { expect, test } from "@playwright/test";

test("French stop confirmation explains exact deletion and supports cancel without changing task identity", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-runs-agentrundetail--french-recovery&viewMode=story"
  );
  await page.getByRole("button", { name: "Arrêter l’agent", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog.getByText("Arrêter cette tâche d’agent ?", { exact: true })).toBeVisible();
  await expect(
    dialog.getByText(/L’exécution n’est marquée comme annulée qu’après confirmation/)
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Annuler", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(
    page.getByText("7f3c2a10-4d5e-4f60-9a1b-2c3d4e5f6a7b", { exact: true }).first()
  ).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
});

test("Japanese failure guidance leaves raw failure diagnostics literal and removes terminal stop actions", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-runs-agentrundetail--japanese-failure&viewMode=story"
  );
  await expect(page.getByText("実行に失敗しました", { exact: true })).toBeVisible();
  await expect(
    page.getByText(
      'spawn failed: secret "agents/org-1/bdr-outreach/crm-token" not found in the agent secret store',
      { exact: true }
    )
  ).toBeVisible();
  await expect(
    page.getByText(
      "Pod のログはありません。Pod の起動前に実行が失敗しました。理由は左側に表示されています。",
      { exact: true }
    )
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "エージェントを停止", exact: true })).toHaveCount(
    0
  );
  await page.getByRole("button", { name: "その他の操作", exact: true }).click();
  await expect(page.getByRole("menuitem", { name: "実行 ID をコピー", exact: true })).toBeVisible();
});

test("French missing-run recovery retains the agent-only destination", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-agents-runs-agentrundetail--french-not-found&viewMode=story"
  );
  await expect(page.getByText("Exécution introuvable", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Cette exécution n’existe peut-être pas, ou vous n’y avez pas accès.", {
      exact: true,
    })
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Ouvrir les exécutions d’agents", exact: true })
  ).toHaveAttribute("href", "/tasks?kind=agent");
});
