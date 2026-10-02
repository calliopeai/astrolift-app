import { expect, test } from "@playwright/test";

test("French comparison localizes controls while keeping both YAML files literal", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-apps-overview-workflowsyncstatuscontrol--french-conflict&viewMode=story"
  );
  await expect(page.getByText("les deux ont changé", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Comparer le fichier du dépôt/ }).click();
  await expect(page.getByText("Dans le dépôt", { exact: true })).toBeVisible();
  await expect(page.getByText("Ce qui serait envoyé", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
});
test("Japanese pull retains a localized confirmation and cancel path", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-apps-overview-workflowsyncstatuscontrol--japanese-pull&viewMode=story"
  );
  await page.getByRole("button", { name: "リポジトリから取り込む", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(
    dialog.getByText("リポジトリのワークフローファイルを取り込みますか？")
  ).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "リポジトリから取り込む", exact: true })
  ).toBeEnabled();
  await dialog.getByRole("button", { name: "キャンセル", exact: true }).click();
  await expect(dialog).not.toBeVisible();
});
test("pending sync disables writes; unknown long state stays bounded and readonly viewers have no writes", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-apps-overview-workflowsyncstatuscontrol--loading&viewMode=story"
  );
  for (const name of ["Check", "Pull from repo", "Push to repo"])
    await expect(page.getByRole("button", { name, exact: true })).toBeDisabled();
  await page.goto(
    "/iframe.html?id=screens-apps-overview-workflowsyncstatuscontrol--long-strings&viewMode=story"
  );
  await expect(page.getByText("future_state_".repeat(30), { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
  await page.goto(
    "/iframe.html?id=screens-apps-overview-workflowsyncstatuscontrol--read-only&viewMode=story"
  );
  await expect(page.getByRole("button", { name: "Push to repo", exact: true })).toHaveCount(0);
});
