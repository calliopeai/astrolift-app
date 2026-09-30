import { expect, test } from "@playwright/test";

for (const state of ["metadata-loading", "metadata-unavailable", "configured-window"] as const) {
  test(`deploy-token rotation ${state} keeps configuration and admission truthful`, async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(
      `/iframe.html?id=screens-apps-secrets-deploytokensscreen--rotation-${state}&viewMode=story`
    );
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toBeVisible();
    const confirm = dialog.getByRole("button", { name: "Rotate token" });
    if (state === "configured-window") {
      await expect(dialog.getByText(/Configured grace window: 1m 30s/)).toBeVisible();
      await expect(dialog.getByText(/Rotation rechecks access/)).toBeVisible();
      await expect(confirm).toBeEnabled();
    } else {
      await expect(confirm).toBeDisabled();
      await expect(
        dialog.getByRole(state === "metadata-loading" ? "status" : "alert")
      ).toBeVisible();
      await expect(dialog.getByText(/Configured grace window:/)).toHaveCount(0);
      if (state === "metadata-unavailable") {
        await dialog.getByRole("button", { name: "Retry" }).click();
        await expect(dialog.getByRole("alert")).toBeVisible();
        await expect(confirm).toBeDisabled();
      }
    }
    const frame = await dialog.boundingBox();
    expect(frame).not.toBeNull();
    expect(frame!.width).toBeLessThanOrEqual(768);
    expect(frame!.x).toBeGreaterThanOrEqual(0);
    expect(frame!.x + frame!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
    expect(errors).toEqual([]);
  });
}
