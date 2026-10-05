import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import en from "../messages/en.json";

const api = `http://127.0.0.1:${process.env.ROUTE_API_PORT ?? 6172}`;
const domainId = "55555555-5555-4555-8555-555555555555";
const d = en.managedDomains,
  c = en.domainConnections;

async function begin(page: Page, context: BrowserContext, baseURL: string, role: string) {
  expect((await context.request.post(`${api}/observations/reset`)).status()).toBe(204);
  await context.addCookies([
    { name: "sessionid", value: role, url: baseURL },
    { name: "backend_jwt", value: "route-fixture-token", url: baseURL },
  ]);
  await page.routeWebSocket("**/app/gql/config/ws/", (socket) =>
    socket.onMessage((message) => {
      const frame = JSON.parse(String(message));
      if (frame.type === "connection_init") socket.send(JSON.stringify({ type: "connection_ack" }));
      if (frame.type === "ping") socket.send(JSON.stringify({ type: "pong" }));
    })
  );
  page.setDefaultTimeout(15000);
  const runtime: string[] = [],
    operations: string[] = [];
  page.on("pageerror", (error) => runtime.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") runtime.push(message.text());
  });
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes("/gql/")) {
      const body = request.postDataJSON();
      if (typeof body?.operationName === "string") operations.push(body.operationName);
    }
  });
  return { runtime, operations };
}
async function clean(context: BrowserContext, runtime: string[]) {
  expect(runtime).toEqual([]);
  const metadata = await (await context.request.get(`${api}/observations`)).json();
  expect(metadata.errors).toEqual([]);
  expect(metadata.mutations).toBe(0);
  expect(await (await context.request.get(`${api}/observations/domain-writes`)).json()).toEqual([]);
}
async function screenshot(page: Page, name: string) {
  if (!process.env.DNS_PERMISSION_UI_PROOF) return;
  await mkdir(process.env.DNS_PERMISSION_UI_PROOF, { recursive: true });
  await page.screenshot({
    path: `${process.env.DNS_PERMISSION_UI_PROOF}/${name}.png`,
    fullPage: true,
  });
}

test("reader sees honest provider refusal and empty DNS/ICMP results without protected reads", async ({
  page,
  context,
}, info) => {
  const { runtime, operations } = await begin(page, context, info.project.use.baseURL!, "reader");
  await page.goto(`/domains/${domainId}`);
  await expect(page.getByRole("button", { name: c.verify, exact: true })).toBeDisabled();
  await page.getByRole("tab", { name: d.records, exact: true }).click();
  await expect(page.getByText(d.platformOperatorRequired, { exact: true })).toBeVisible();
  await expect(page.getByText("PLATFORM_OPERATOR_REQUIRED", { exact: true })).toBeVisible();
  await expect(page.getByText("origin.acme.example", { exact: true })).toHaveCount(0);
  await screenshot(page, "dns-reader-records-1440");
  await page.getByRole("tab", { name: d.diagnostics, exact: true }).click();
  await page.getByRole("button", { name: d.run, exact: true }).click();
  const response = page.getByRole("region", { name: d.response, exact: true });
  await expect(response.getByText(d.dnsNoDataStatus, { exact: true })).toBeVisible();
  await expect(response.getByText(d.dnsNoData, { exact: true })).toBeVisible();
  await expect(response.getByText("DNS_NO_DATA", { exact: true })).toBeVisible();
  await expect(response.getByText(d.ok, { exact: true })).toHaveCount(0);
  await screenshot(page, "dns-reader-no-data-1440");
  await page.getByLabel(d.tool, { exact: true }).selectOption("PING");
  await page.getByRole("button", { name: d.run, exact: true }).click();
  await expect(response.getByText(d.icmpTimeout, { exact: true })).toBeVisible();
  await expect(response.getByText("ICMP_TIMEOUT", { exact: true })).toBeVisible();
  await expect(response.getByText(d.ok, { exact: true })).toHaveCount(0);
  expect(operations).toContain("DnsConnectionSupport");
  expect(operations).not.toContain("DnsDomainBinding");
  await clean(context, runtime);
});

test("reader mobile wizard explains operator permission without account inventory", async ({
  page,
  context,
}, info) => {
  await page.setViewportSize({ width: 390, height: 1000 });
  const { runtime, operations } = await begin(page, context, info.project.use.baseURL!, "reader");
  await page.goto("/domains/connect");
  await expect(page.getByText(d.platformOperatorRequired, { exact: true })).toBeVisible();
  await expect(page.getByText("PLATFORM_OPERATOR_REQUIRED", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Cloudflare", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: d.refresh, exact: true }).click();
  await expect(page.getByText(d.platformOperatorRequired, { exact: true })).toBeVisible();
  expect((await page.locator("#main-content").boundingBox())!.width).toBeGreaterThan(360);
  await screenshot(page, "dns-reader-wizard-390");
  expect(operations).not.toContain("DnsConnectionsPage");
  expect(operations).not.toContain("DnsDomainBinding");
  expect(operations).not.toContain("CloudflareZones");
  expect(operations).not.toContain("CloudflareRecords");
  await clean(context, runtime);
});

test("reader domain email entry does not issue protected domain binding", async ({
  page,
  context,
}, info) => {
  await page.setViewportSize({ width: 390, height: 1000 });
  const { runtime, operations } = await begin(page, context, info.project.use.baseURL!, "reader");
  await page.goto(`/domains/${domainId}/email`);
  await expect(
    page.getByRole("heading", { name: en.emailDelivery.domainTitle, exact: true })
  ).toBeVisible();
  await expect(page.getByRole("link", { name: en.installAlertMail.open, exact: true })).toHaveCount(
    0
  );
  await expect.poll(() => operations.includes("DnsConnectionSupport")).toBe(true);
  expect(operations).not.toContain("DnsDomainBinding");
  await screenshot(page, "dns-reader-email-390");
  await clean(context, runtime);
});

test("operator retains exact pending verification and provider inventory", async ({
  page,
  context,
}, info) => {
  const { runtime, operations } = await begin(page, context, info.project.use.baseURL!, "owner");
  await page.goto(`/domains/${domainId}`);
  await expect(page.getByRole("button", { name: c.verify, exact: true })).toBeEnabled();
  await page.getByRole("tab", { name: d.records, exact: true }).click();
  await expect(page.getByText("origin.acme.example", { exact: true })).toBeVisible();
  expect(operations).toContain("DnsDomainBinding");
  expect(operations).toContain("DnsConnectionSupport");
  await clean(context, runtime);
});
