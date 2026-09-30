import { expect, test, type Page } from "@playwright/test";

async function source(page: Page, state: string) {
  await page.goto(
    `/iframe.html?id=screens-agents-detail-agentsecretsource--${state}&viewMode=story`
  );
  await expect(page.getByRole("heading", { name: "Environment spec secrets" })).toBeVisible();
}

test("an unselected source does not mount secret editors or claim a dispatch default", async ({
  page,
}) => {
  await source(page, "unselected");
  await expect(page.getByText("Confirmed recipe values and bundles.")).toHaveCount(0);
  await expect(
    page.getByText(/Select the recipe separately when dispatching an agent/)
  ).toBeVisible();
  await expect(page.getByRole("combobox")).toHaveText("Choose an environment spec");
});

test("an unavailable source closes editors while preserving an explicit way to choose again", async ({
  page,
}) => {
  await source(page, "unavailable");
  await expect(page.getByText(/selected spec is no longer available/)).toBeVisible();
  await expect(page.getByText("Confirmed recipe values and bundles.")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Clear source" })).toBeEnabled();
});

test("a confirmed long recipe fits the narrow screen and labels shared changes", async ({
  page,
}) => {
  await page.setViewportSize({ width: 480, height: 900 });
  await source(page, "long-strings");
  await expect(page.getByText("Confirmed recipe values and bundles.")).toBeVisible();
  await expect(page.getByText(/Edits affect every run or box using this recipe/)).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth
  );
  expect(overflow).toBe(false);
});
