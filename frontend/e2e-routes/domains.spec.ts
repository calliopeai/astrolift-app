import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import en from "../messages/en.json";
const api = `http://127.0.0.1:${process.env.ROUTE_API_PORT ?? 6172}`;
const domainId = "55555555-5555-4555-8555-555555555555";
const d = en.managedDomains,
  c = en.domainConnections;
async function begin(page: Page, context: BrowserContext, baseURL: string, role = "owner") {
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
}
async function clean(page: Page, context: BrowserContext, action: () => Promise<void>) {
  const runtime: string[] = [];
  page.on("pageerror", (e) => runtime.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") runtime.push(m.text());
  });
  await action();
  expect(runtime).toEqual([]);
  const metadata = await (await context.request.get(`${api}/observations`)).json();
  expect(metadata.errors).toEqual([]);
  expect(metadata.mutations).toBe(0);
}
test("rendered domain navigation registers only read-only discovery then separately verifies TXT", async ({
  page,
  context,
}, info) => {
  await begin(page, context, info.project.use.baseURL!);
  await clean(page, context, async () => {
    await page.goto("/domains");
    await expect(page.getByRole("heading", { name: d.title, exact: true })).toBeVisible();
    await page.getByRole("link", { name: c.title, exact: true }).click();
    await expect(page).toHaveURL(/\/domains\/connect$/);
    await page.getByRole("button", { name: "Cloudflare", exact: true }).click();
    await page.getByRole("button", { name: c.chooseConnection, exact: true }).click();
    await page.getByRole("button", { name: c.selectZone, exact: true }).click();
    await expect(page.getByText("origin.acme.example", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: c.register, exact: true })).toBeDisabled();
    await page.getByRole("checkbox", { name: c.acknowledge, exact: true }).check();
    await page.getByRole("button", { name: c.register, exact: true }).click();
    await expect(page.getByText(c.registrationAccepted, { exact: true })).toBeVisible();
    await page.getByRole("button", { name: c.verify, exact: true }).click();
    await expect(page.getByText(c.ownershipVerified, { exact: true })).toBeVisible();
    await page.getByRole("link", { name: c.openDomain, exact: true }).click();
    await expect(page).toHaveURL(/\/domains\/77777777-7777-4777-8777-777777777777$/);
    await expect(
      page.getByRole("heading", { name: "cloudflare.example", exact: true })
    ).toBeVisible();
    await page.getByRole("tab", { name: d.records, exact: true }).click();
    await expect(page.getByText(d.cloudflareBinding, { exact: true })).toBeVisible();
    await expect(
      page.getByRole("list", { name: d.records, exact: true }).locator(":scope > li")
    ).toHaveCount(1);
    expect(await (await context.request.get(`${api}/observations/domain-writes`)).json()).toEqual([
      {
        field: "registerCloudflareDnsZone",
        input: {
          connectionId: "66666666-6666-4666-8666-666666666666",
          expectedConnectionVersion: 3,
          zoneId: "0123456789abcdef0123456789abcdef",
          zoneName: "cloudflare.example",
        },
      },
      { field: "verifyManagedDomain", input: { zone: "cloudflare.example" } },
    ]);
  });
});
test("Route53 provisioning and effective declarations remain separate from public DNS and actual tools", async ({
  page,
  context,
}, info) => {
  await begin(page, context, info.project.use.baseURL!);
  await clean(page, context, async () => {
    await page.goto(`/domains/${domainId}`);
    await expect(page.getByText(d.provisioned, { exact: true })).toBeVisible();
    await expect(page.getByText(d.mismatchHelp, { exact: true })).toBeVisible();
    await expect(
      page
        .getByRole("region", { name: `${d.effectiveTenant} · server_configuration`, exact: true })
        .getByText(d.defaultHelp, { exact: true })
    ).toBeVisible();
    await expect(page.getByText("ns-0.awsdns-00.com", { exact: true })).toBeVisible();
    await page.getByRole("tab", { name: d.routing, exact: true }).click();
    await expect(page.getByText(d.routeHelp, { exact: true })).toBeVisible();
    await page.getByRole("tab", { name: d.diagnostics, exact: true }).click();
    await page.getByLabel(d.tool, { exact: true }).selectOption("DIG");
    await page.getByLabel(d.recordType, { exact: true }).selectOption("SRV");
    await page.getByRole("button", { name: d.run, exact: true }).click();
    await expect(page.getByText("controlled-dns-answer", { exact: true })).toBeVisible();
    await expect(page.getByText(d.tlsVerified, { exact: true })).toBeVisible();
    expect(await (await context.request.get(`${api}/observations/domain-writes`)).json()).toEqual(
      []
    );
  });
});
test("reader action hints cannot enable writes or Cloudflare account setup", async ({
  page,
  context,
}, info) => {
  await begin(page, context, info.project.use.baseURL!, "reader");
  await clean(page, context, async () => {
    await page.goto(`/domains/${domainId}`);
    await expect(page.getByRole("button", { name: d.delete, exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: d.revalidate, exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: c.verify, exact: true })).toBeDisabled();
    await page.getByRole("link", { name: c.title, exact: true }).click();
    await expect(page.getByRole("button", { name: "Cloudflare", exact: true })).toBeDisabled();
    expect(await (await context.request.get(`${api}/observations/domain-writes`)).json()).toEqual(
      []
    );
  });
});
