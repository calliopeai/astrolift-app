import { expect, test } from "@playwright/test";

test("French review uses localized approvals and retains exact image data", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-deployments-startdeploymentpage--french-review&viewMode=story"
  );
  await expect(
    page.getByText("Cette action lance un déploiement. Vérifiez ce qui va être déployé.")
  ).toBeVisible();
  await expect(page.getByText(/nécessite 2 approbations/)).toBeVisible();
  await expect(page.getByText(/Les déploiements vers .* sont en pause/)).toBeVisible();
  await expect(page.getByText("sha-literal")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retour", exact: true })).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
});
test("Japanese wizard validation retains focus and recovery controls", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-deployments-startdeploymentpage--japanese-validation&viewMode=story"
  );
  await page.getByRole("button", { name: "次へ", exact: true }).click();
  await expect(page.getByText("デプロイするアプリを選択してください。")).toBeVisible();
  await expect(page.getByText("環境を選択してください。")).toBeVisible();
  await expect(page.getByRole("combobox", { name: "アプリ" })).toHaveAttribute(
    "aria-invalid",
    "true"
  );
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
});
