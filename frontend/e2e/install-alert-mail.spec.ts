import { test, expect } from "@playwright/test";
import en from "../messages/en.json";
const c = en.installAlertMail;

test("SMTP stories keep current admission, unknown acceptance and narrow own-mailbox review clear", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-settings-install-alert-email--unsupported&viewMode=story"
  );
  await expect(page.getByRole("button", { name: c.send, exact: true })).toBeDisabled();
  await page.goto("/iframe.html?id=screens-settings-install-alert-email--unknown&viewMode=story");
  await expect(page.getByText(c.uncertain, { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: c.another, exact: true })).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 1000 });
  await page.goto("/iframe.html?id=screens-settings-install-alert-email--accepted&viewMode=story");
  await expect(page.getByText(c.acceptance, { exact: true })).toBeVisible();
  await expect(page.getByRole("textbox")).toHaveCount(0);
});

test("real client story loads current actor/org support and cursor history", async ({ page }) => {
  const errors: string[] = [];
  page.on("response", (r) => {
    if (r.status() >= 400) console.log("Owned Storybook failed asset", r.url(), r.status());
  });
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  await page.goto(
    "/iframe.html?id=screens-settings-install-alert-email-adapter--current-source&viewMode=story"
  );
  await expect(page.getByRole("button", { name: c.review, exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: c.send, exact: true })).toBeDisabled();
  expect(errors).toEqual([]);
});
