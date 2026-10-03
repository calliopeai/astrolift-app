import { expect, test } from "@playwright/test";
import fr from "../messages/fr.json";
import ja from "../messages/ja.json";

test("French settings missing-spec guidance remains localized without update controls", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-detail-agentmodelaccess--french-empty&viewMode=story"
  );
  for (const key of ["title", "liveTitle"] as const)
    await expect(page.getByText(fr.agentModelAccess[key], { exact: true })).toBeVisible();
  await expect(page.getByText(fr.agentModelAccess.noSpec, { exact: true })).toHaveCount(2);
  await expect(page.getByRole("switch")).toHaveCount(0);
});
test("Japanese settings refused-read notice preserves the literal diagnostic and recovery action", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-detail-agentmodelaccess--japanese-read-failed&viewMode=story"
  );
  await expect(page.getByRole("alert")).toContainText(ja.agentModelAccess.readFailed);
  await expect(page.getByText("RAW_ENV_SPEC_DIAGNOSTIC", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: ja.agentModelAccess.retry, exact: true })
  ).toBeVisible();
  await expect(page.getByText(ja.agentModelAccess.noSpec, { exact: true })).toHaveCount(0);
  await expect(page.getByRole("switch")).toHaveCount(0);
});
for (const control of [
  {
    story: "managedmodelsectionview",
    label: "modelLabel",
    description: "modelDescription",
    enable: "modelEnable",
    disable: "modelDisable",
  },
  {
    story: "vncsessionsectionview",
    label: "vncLabel",
    description: "vncDescription",
    enable: "vncEnable",
    disable: "vncDisable",
  },
] as const) {
  test(`French ${control.story} preserves an accessible unchecked switch and localized instructions`, async ({
    page,
  }) => {
    await page.goto(
      `/iframe.html?id=screens-agents-list-${control.story}--french-off&viewMode=story`
    );
    await expect(page.getByText(fr.agentModelAccess[control.label], { exact: true })).toBeVisible();
    await expect(
      page.getByText(fr.agentModelAccess[control.description], { exact: true })
    ).toBeVisible();
    await expect(
      page.getByRole("switch", { name: fr.agentModelAccess[control.enable], exact: true })
    ).toHaveAttribute("aria-checked", "false");
    await expect(page.getByText(fr.agentModelAccess.off, { exact: true })).toBeVisible();
  });
  test(`Japanese ${control.story} blocks interaction during a pending write`, async ({ page }) => {
    await page.goto(
      `/iframe.html?id=screens-agents-list-${control.story}--japanese-saving&viewMode=story`
    );
    const toggle = page.getByRole("switch", {
      name: ja.agentModelAccess[control.disable],
      exact: true,
    });
    await expect(toggle).toBeDisabled();
    await expect(toggle).toHaveAttribute("aria-checked", "true");
    await expect(page.getByText(ja.agentModelAccess.on, { exact: true })).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
    ).toBe(true);
  });
}
