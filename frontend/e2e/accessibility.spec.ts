import { expect, test } from "@playwright/test";

const story = (id: string) => `/iframe.html?id=${id}&viewMode=story`;

test("app cards keep app, pin and deployment keyboard actions separate", async ({ page }) => {
  await page.goto(story("screens-apps-list-appslistscreen--cards"));
  const app = page.getByRole("link", { name: "Billing Worker", exact: true });
  await expect(app).toBeVisible();
  await app.focus();
  await page.keyboard.press("Tab");
  const pin = page.getByRole("button", { name: "Unpin app", exact: true });
  await expect(pin).toBeFocused();
  const originalUrl = page.url();
  await page.keyboard.press("Space");
  await expect(page).toHaveURL(originalUrl);
  await page.keyboard.press("Tab");
  const failed = page.getByRole("link", { name: "View failed deploy", exact: true });
  await expect(failed).toBeFocused();
  expect(await page.locator("a a, a button").count()).toBe(0);
  await expect(app).toHaveAttribute("href", "/apps/billing-worker");
  await expect(failed).toHaveAttribute("href", "/deployments/d0000000-0000-4000-8000-000000000002");
  const bodyTarget = await page
    .getByRole("listitem")
    .filter({ has: page.getByRole("link", { name: "API Gateway", exact: true }) })
    .getByText("Edge router for every public API, with rate limits and auth.", { exact: true })
    .evaluate((element) => {
      const box = element.getBoundingClientRect();
      return document
        .elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)
        ?.closest("a")
        ?.getAttribute("href");
    });
  expect(bodyTarget).toBe("/apps/api-gateway");
  const failedTarget = await failed.evaluate((element) => {
    const box = element.getBoundingClientRect();
    return document
      .elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)
      ?.closest("a")
      ?.getAttribute("href");
  });
  expect(failedTarget).toBe("/deployments/d0000000-0000-4000-8000-000000000002");
});

test("email bounce and complaint details work with keyboard and retain disclosure focus", async ({
  page,
}) => {
  await page.goto(story("screens-apps-managedservices-emaildetailsheet--messages"));
  const bounce = page.getByRole("button", { name: "bounced@customer.example.com", exact: true });
  await expect(bounce).toBeVisible();
  await bounce.focus();
  await page.keyboard.press("Enter");
  const details = page.getByRole("region", {
    name: "Message details for bounced@customer.example.com",
  });
  await expect(details.getByText("Permanent", { exact: true })).toBeVisible();
  await expect(bounce).toHaveAttribute("aria-expanded", "true");
  const complaint = page.getByRole("button", { name: "angry@customer.example.com", exact: true });
  await complaint.focus();
  await page.keyboard.press("Space");
  await expect(
    page
      .getByRole("region", { name: "Message details for angry@customer.example.com" })
      .getByText("abuse", { exact: true })
  ).toBeVisible();
  await bounce.focus();
  await page.keyboard.press("Enter");
  await expect(details).toHaveCount(0);
  await expect(bounce).toBeFocused();
});

test("webhook secret dialog traps focus, announces its purpose and returns focus on Escape", async ({
  page,
}) => {
  await page.goto(story("screens-settings-sourceproviders-sourceprovidersscreen--secret-keyboard"));
  const opener = page.getByRole("button", { name: "Show rotated secret", exact: true });
  await expect(opener).toBeVisible();
  await expect(opener).toBeFocused();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Webhook secret generated", exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAttribute("aria-modal", "true");
  await expect(dialog).toHaveAccessibleDescription(/plaintext secret is shown exactly once/);
  const copy = dialog.getByRole("button", { name: "Copy URL", exact: true });
  await expect(copy).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "Close", exact: true })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(copy).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
});

test("rotation confirmation hands off to the reveal and returns focus to source connections", async ({
  page,
}) => {
  await page.goto(story("screens-settings-sourceproviders-sourceprovidersscreen--full"));
  await page.getByRole("button", { name: "Connections: row actions", exact: true }).first().click();
  await page.getByRole("menuitem", { name: "Webhook secret", exact: true }).click();
  await page.getByRole("button", { name: "Rotate secret", exact: true }).click();
  const reveal = page.getByRole("dialog", { name: "Webhook secret generated", exact: true });
  await expect(reveal).toBeVisible();
  await expect(reveal.getByRole("button", { name: "Copy URL", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(reveal).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Source connections", exact: true })).toBeFocused();
});
