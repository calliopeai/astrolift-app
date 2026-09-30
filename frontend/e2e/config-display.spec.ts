import { expect, test } from "@playwright/test";

test("manifest conflict choice describes retaining a draft", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-apps-config-configeditorscreen--conflict&viewMode=story"
  );
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("unsaved draft for the next Save");
  await expect(dialog.getByRole("button", { name: "Keep mine", exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Force overwrite" })).toHaveCount(0);
});

test("workload controls do not advertise unobserved ready pods", async ({ page }) => {
  await page.goto("/iframe.html?id=screens-apps-controls-controlssection--full&viewMode=story");
  await expect(page.getByText("—/3 ready", { exact: true })).toHaveCount(2);
  await expect(page.getByText("3/3 ready", { exact: true })).toHaveCount(0);
});
