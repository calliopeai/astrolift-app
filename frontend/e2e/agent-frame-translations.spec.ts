import { expect, test } from "@playwright/test";

test("French agent frame translates tabs and retains a refused run confirmation", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-detail-agentframe--french-run-refused&viewMode=story"
  );
  const nav = page.getByRole("navigation", { name: "Sections de l’agent", exact: true });
  await expect(nav.getByRole("link", { name: "Vue d’ensemble", exact: true })).toHaveAttribute(
    "aria-current",
    "page"
  );
  await expect(nav.getByRole("link", { name: "Exécutions", exact: true })).toHaveAttribute(
    "href",
    "/agents/research-scout/runs"
  );
  await page.getByRole("button", { name: "Exécuter maintenant", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByText("Exécuter Research Scout maintenant", { exact: true })
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Exécuter maintenant", exact: true }).click();
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Annuler", exact: true }).click();
  await expect(dialog).not.toBeVisible();
});

test("Japanese input refusal retains literal message draft instead of claiming it was sent", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-detail-agentoverview--japanese-input-refused&viewMode=story"
  );
  const input = page.getByRole("textbox", { name: "監督者へのメッセージ", exact: true });
  await input.fill("LITERAL_MESSAGE_内容");
  await page.getByRole("button", { name: "送信", exact: true }).click();
  await expect(input).toHaveValue("LITERAL_MESSAGE_内容");
  await expect(
    page.getByText("キューに追加され、エージェントの次のターン開始時に配信されます。", {
      exact: true,
    })
  ).toBeVisible();
});

test("French load errors and unknown long run modes retain literal diagnostics within viewport", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-detail-agentframe--french-load-error&viewMode=story"
  );
  await expect(page.getByText("Impossible de charger cet agent", { exact: true })).toBeVisible();
  await expect(page.getByText("RAW_SERVER_DIAGNOSTIC", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Réessayer", exact: true })).toBeVisible();
  await page.goto("/iframe.html?id=screens-agents-detail-agentframe--long-strings&viewMode=story");
  await expect(
    page.getByText(/long_running_custom_family · event_driven_custom_mode/)
  ).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
});
