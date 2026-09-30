import { expect, test } from "@playwright/test";

test("pending webhook disables only its own writes", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-webhooks-webhooksscreen--one-row-pending&viewMode=story"
  );
  await page
    .getByRole("row")
    .filter({ hasText: "collector.acme.dev" })
    .getByRole("button", { name: /row actions/ })
    .click();
  await expect(page.getByRole("menuitem", { name: "Rotate secret" })).toHaveAttribute(
    "aria-disabled",
    "true"
  );
  await expect(page.getByRole("menuitem", { name: "Send test event" })).toHaveAttribute(
    "aria-disabled",
    "true"
  );
  await page.keyboard.press("Escape");
  await page
    .getByRole("row")
    .filter({ hasText: "hooks.slack.com" })
    .getByRole("button", { name: /row actions/ })
    .click();
  await expect(page.getByRole("menuitem", { name: "Rotate secret" })).not.toHaveAttribute(
    "aria-disabled",
    "true"
  );
  await expect(page.getByRole("menuitem", { name: "Send test event" })).not.toHaveAttribute(
    "aria-disabled",
    "true"
  );
});

test("command admission requires a container", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-apps-tools-commandrunnerscreen--missing-container&viewMode=story"
  );
  await expect(page.getByRole("button", { name: "Run", exact: true })).toBeDisabled();
});

test("uppercase running task supports overseer messages", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-agents-detail-agentoverview--uppercase-running&viewMode=story"
  );
  await expect(page.getByPlaceholder("Send a follow-up to the running agent…")).toBeVisible();
});
