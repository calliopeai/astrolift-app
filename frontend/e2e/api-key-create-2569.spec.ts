import { expect, test } from "@playwright/test";

/**
 * #2569: the scope catalog makes the API key create form taller than the
 * window, and the sheet does not scroll, so "Create token" sat below the
 * screen. A click there landed outside the sheet, which closed it without
 * creating anything and without an error. The form scrolls inside the sheet.
 */
test("API key create sheet reaches Create token and submits", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/iframe.html?id=screens-tokens-tokensscreen--create-sheet&viewMode=story");
  const sheet = page.getByRole("dialog");
  await expect(sheet).toBeVisible();
  await sheet.getByLabel("Name", { exact: true }).fill("ci-deploy");

  const create = sheet.getByRole("button", { name: "Create token", exact: true });
  await create.scrollIntoViewIfNeeded();
  await expect(create).toBeInViewport();
  // Playwright refuses a click that another element would receive, so this
  // lands on the button and submits; the story's onCreate succeeds and the
  // sheet closes.
  await create.click();
  await expect(sheet).toBeHidden();
  expect(errors).toEqual([]);
});
