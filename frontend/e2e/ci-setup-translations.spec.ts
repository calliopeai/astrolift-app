import { expect, test } from "@playwright/test";

test("French CI setup translates validation and credential controls while retaining identifiers", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-apps-overview-cisetupsection--french-validation&viewMode=story"
  );
  await expect(page.getByText("Configuration CI", { exact: true })).toBeVisible();
  await expect(page.getByText("Valider les secrets CI", { exact: true })).toBeVisible();
  await expect(page.getByText("défini, obsolète", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("non défini", { exact: true }).first()).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Gérer les jetons de déploiement", exact: true })
  ).toHaveAttribute("href", "/apps/checkout/tokens");
  await expect(
    page.getByText("ASTROLIFT_WORKLOAD_IDENTITY_PROVIDER", { exact: true }).first()
  ).toBeVisible();
  await expect(
    page.getByText(
      "projects/123456789012/locations/global/workloadIdentityPools/astrolift/providers/github",
      { exact: true }
    )
  ).toBeVisible();
  await page.getByText("Workflow de référence GitHub Actions", { exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Copier le YAML du workflow", exact: true })
  ).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
});

test("Japanese token rotation warning preserves the confirmation and cancel path", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-apps-overview-cisetupsection--japanese-rotate&viewMode=story"
  );
  await page.getByRole("button", { name: "送信して更新", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(
    dialog.getByText("CI シークレットをリポジトリに送信しますか？", { exact: true })
  ).toBeVisible();
  await expect(
    dialog.getByText("デプロイトークンが更新されます。以前のトークンは直ちに使えなくなります。", {
      exact: true,
    })
  ).toBeVisible();
  await expect(dialog.getByRole("button", { name: "送信して更新", exact: true })).toBeEnabled();
  await dialog.getByRole("button", { name: "キャンセル", exact: true }).click();
  await expect(dialog).not.toBeVisible();
});

test("Japanese agent delivery retains the source-only path and French malformed dates remain readable", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-apps-overview-cisetupsection--japanese-agent&viewMode=story"
  );
  await expect(page.getByText("エージェントのソース配信", { exact: true })).toBeVisible();
  await expect(
    page.getByText(".github/workflows/astrolift-agent-bdr-agent.yml", { exact: true })
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "送信して更新", exact: true })).toHaveCount(0);
  await page.goto(
    "/iframe.html?id=screens-apps-overview-cisetupsection--french-malformed-date&viewMode=story"
  );
  await expect(page.getByText("LITERAL_SECRET_NAME", { exact: true })).toBeVisible();
  await expect(page.getByText("Date indisponible", { exact: true })).toBeVisible();
  await expect(page.getByText("Invalid Date", { exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
});
