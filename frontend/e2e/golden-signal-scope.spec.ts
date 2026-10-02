import { expect, test } from "@playwright/test";

for (const width of [816, 1280]) {
  test(`mixed measurements disclose target without hiding available charts at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(
      "/iframe.html?id=patterns-observability-goldensignalspanel--mixed-availability&viewMode=story"
    );
    await expect(
      page.getByText("Resource limits are missing; saturation is unavailable.")
    ).toBeVisible();
    await expect(page.getByText("Scope: Workload")).toHaveCount(8);
    await expect(page.getByText("production · checkout-production · api")).toHaveCount(8);
    await expect(page.locator(".recharts-line").first()).toBeVisible();
    await page.getByText("Canonical identities", { exact: true }).first().click();
    await expect(page.getByText("019eb737-0100-7000-8000-000000000005").first()).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)
    ).toBe(false);
    expect(errors).toEqual([]);
  });
}
