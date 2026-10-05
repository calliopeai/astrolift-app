import { expect, test } from "@playwright/test";
import en from "../messages/en.json";
const c = en.emailDelivery;
test("mail stories retain current authority and honest observations", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-emaildelivery-emaildeliverypanel--permission-denied&viewMode=story"
  );
  await expect(page.getByRole("button", { name: c.send, exact: true })).toBeDisabled();
  await page.goto(
    "/iframe.html?id=screens-emaildelivery-emaildeliverypanel--accepted&viewMode=story"
  );
  await expect(page.getByText(c.acceptedHelp, { exact: true })).toBeVisible();
  await page.goto(
    "/iframe.html?id=screens-emaildelivery-domainemailscreen--dns-no-data&viewMode=story"
  );
  await expect(page.getByText(c.recordsEmpty, { exact: true })).toBeVisible();
  await page.getByLabel(c.selector, { exact: true }).fill("reviewed-selector");
  await page.getByLabel(c.dkimRecordType, { exact: true }).selectOption("CNAME");
  await expect(page.getByRole("button", { name: c.dkim, exact: true })).toBeEnabled();
});
