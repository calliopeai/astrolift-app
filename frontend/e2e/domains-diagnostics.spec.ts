import { expect, test } from "@playwright/test";
import en from "../messages/en.json";
const d = en.managedDomains;

const story = (id: string) => `/iframe.html?id=screens-domains-${id}&viewMode=story`;

test("domain detail changes one active panel and preserves truthful public delegation", async ({
  page,
}) => {
  await page.goto(story("manageddomaindetail--full"));
  await expect(page.getByRole("tab", { name: "Overview", exact: true })).toHaveAttribute(
    "aria-selected",
    "true"
  );
  await expect(page.getByText("Provisioned", { exact: true })).toBeVisible();
  await expect(page.getByText(d.mismatchHelp)).toBeVisible();
  await expect(page.getByRole("tabpanel")).toHaveCount(1);
  await page.getByRole("tab", { name: "Records", exact: true }).click();
  await expect(page.getByRole("tabpanel")).toHaveCount(1);
  await expect(page.getByLabel(d.recordSearch)).toBeVisible();
  await page.getByLabel(d.recordSearch).fill("hostmaster");
  await expect(
    page.getByRole("list", { name: "Records", exact: true }).locator(":scope > li")
  ).toHaveCount(1);
  await page.getByRole("tab", { name: "Records", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Routing", exact: true })).toHaveAttribute(
    "aria-selected",
    "true"
  );
  await expect(page.getByText(d.routeHelp)).toBeVisible();
  await expect(page.getByRole("tabpanel")).toHaveCount(1);
});

test("unknown and denied detail states cannot appear healthy or actionable", async ({ page }) => {
  await page.goto(story("manageddomaindetail--read-only"));
  await expect(page.getByRole("button", { name: d.revalidate })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Remove domain" })).toBeDisabled();
  await page.goto(story("manageddomaindetail--read-failed"));
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByRole("tab", { name: "Overview" })).toHaveCount(0);
  await page.goto(story("manageddomaindetail--unsupported-provider"));
  await page.getByRole("tab", { name: "Records", exact: true }).click();
  await expect(page.getByText("Unsupported", { exact: true })).toBeVisible();
});

test("DNS setup review requires explicit read-only acknowledgement", async ({ page }) => {
  await page.goto(story("dnsconnectionwizard--review"));
  await expect(page.getByRole("button", { name: "Register read-only zone" })).toBeDisabled();
  await expect(
    page.getByText(
      "Registration records a read-only provider binding. It does not create DNS records, request certificates, change delegation or make apps reachable."
    )
  ).toBeVisible();
  await page.getByRole("checkbox").check();
  await expect(page.getByRole("button", { name: "Register read-only zone" })).toBeEnabled();
  await expect(page.getByText("origin.acme.example", { exact: true })).toBeVisible();
  await page.goto(story("dnsconnectionwizard--partial-records"));
  await expect(page.getByRole("checkbox")).toBeDisabled();
  await expect(page.getByRole("button", { name: "Register read-only zone" })).toBeDisabled();
});

test("DNS setup token input clears on navigation and OAuth stays unavailable when unconfigured", async ({
  page,
}) => {
  await page.goto(story("dnsconnectionwizard--connections"));
  await page.getByLabel("Connection name", { exact: true }).fill("Read-only account");
  await page
    .getByLabel("Cloudflare API token", { exact: true })
    .fill("synthetic-private-token-marker");
  await expect(page.getByRole("button", { name: "Connect with Cloudflare OAuth" })).toBeDisabled();
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(page.getByLabel("Cloudflare API token", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Cloudflare", exact: true }).click();
  await expect(page.getByLabel("Cloudflare API token", { exact: true })).toHaveValue("");
});
