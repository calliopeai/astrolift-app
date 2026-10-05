import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import en from "../messages/en.json";
const api = `http://127.0.0.1:${process.env.ROUTE_API_PORT ?? 6172}`;
const proof = process.env.EMAIL_UI_PROOF;
const c = en.emailDelivery;
for (const width of [1440, 390])
  test(`actual domain mail review and history at ${width}px`, async ({ page, context }, info) => {
    await page.setViewportSize({ width, height: 1000 });
    expect((await context.request.post(`${api}/observations/reset`)).status()).toBe(204);
    await context.addCookies([
      { name: "sessionid", value: "owner", url: info.project.use.baseURL! },
      { name: "backend_jwt", value: "route-fixture-token", url: info.project.use.baseURL! },
    ]);
    await page.routeWebSocket("**/app/gql/config/ws/", (socket) =>
      socket.onMessage((message) => {
        const frame = JSON.parse(String(message));
        if (frame.type === "connection_init")
          socket.send(JSON.stringify({ type: "connection_ack" }));
        if (frame.type === "ping") socket.send(JSON.stringify({ type: "pong" }));
      })
    );
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    await page.goto("/domains/55555555-5555-4555-8555-555555555555");
    await page.getByRole("link", { name: c.domainTitle, exact: true }).click();
    await expect(page).toHaveURL(/\/domains\/55555555-5555-4555-8555-555555555555\/email$/);
    if (width < 768) {
      await expect(page.getByRole("navigation", { name: "Main", exact: true })).toHaveCount(0);
      await expect(page.getByRole("complementary", { name: "Projects", exact: true })).toHaveCount(
        0
      );
      await page.getByRole("button", { name: en.responsiveShell.navigation, exact: true }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await page.getByRole("button", { name: en.responsiveShell.close, exact: true }).click();
      await expect(page.getByRole("dialog")).toHaveCount(0);
      const content = await page.locator("#main-content").boundingBox();
      expect(content!.width).toBeGreaterThan(360);
    }
    await page
      .getByLabel(c.apps, { exact: true })
      .selectOption("b0000000-0000-4000-8000-000000000001");
    await page
      .getByLabel(c.services, { exact: true })
      .selectOption("40000000-0000-4000-8000-000000000001");
    await expect(page.getByText("sender@acme.example", { exact: true })).toBeVisible();
    await expect(page.getByText(c.match, { exact: true })).toBeVisible();
    await page.getByRole("button", { name: c.mx, exact: true }).click();
    await expect(page.getByText("controlled-dns-answer", { exact: true })).toBeVisible();
    await page.getByLabel(c.selector, { exact: true }).fill("reviewed-selector");
    await page.getByLabel(c.dkimRecordType, { exact: true }).selectOption("CNAME");
    await page.getByRole("button", { name: c.dkim, exact: true }).click();
    await expect(
      page.getByText("reviewed-selector._domainkey.acme.example", { exact: true })
    ).toBeVisible();
    await page.getByLabel(c.recipient, { exact: true }).fill("success@simulator.amazonses.com");
    await page.getByRole("button", { name: c.review, exact: true }).click();
    await expect(page.getByRole("button", { name: c.send, exact: true })).toBeEnabled();
    if (proof) {
      await mkdir(proof, { recursive: true });
      await page
        .getByLabel(c.recipient, { exact: true })
        .evaluate((element) => element.scrollIntoView({ block: "start" }));
      await page.screenshot({ path: `${proof}/mail-reviewed-${width}.png`, fullPage: true });
    }
    const emptyDescription = page
      .getByRole("table", { name: c.history, exact: true })
      .getByText(c.intro, { exact: true });
    expect(await emptyDescription.evaluate((element) => getComputedStyle(element).whiteSpace)).toBe(
      "normal"
    );
    await page.getByRole("button", { name: c.send, exact: true }).click();
    await expect(page.getByText(c.acceptedHelp, { exact: true })).toBeVisible();
    await expect(page.getByText(c.simulatorHelp, { exact: true })).toBeVisible();
    await expect(
      page
        .getByRole("table", { name: c.history, exact: true })
        .getByText("Provider accepted", { exact: true })
    ).toBeVisible();
    await expect(
      page
        .getByRole("table", { name: c.history, exact: true })
        .getByText(c.delivered, { exact: true })
    ).toHaveCount(0);
    const writes = await (await context.request.get(`${api}/observations/email-writes`)).json();
    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatchObject({
      managedServiceId: "40000000-0000-4000-8000-000000000001",
      expectedVersion: 7,
      recipient: "success@simulator.amazonses.com",
    });
    expect(writes[0].requestId).toMatch(/^[0-9a-f-]{36}$/);
    if (proof) {
      await page
        .getByText(c.acceptedHelp, { exact: true })
        .evaluate((element) => element.scrollIntoView({ block: "start" }));
      await page.screenshot({ path: `${proof}/mail-history-${width}.png`, fullPage: true });
    }
    await page.goto("/domains/connect");
    await page.getByRole("button", { name: "Cloudflare", exact: true }).click();
    await expect(
      page.getByRole("button", { name: en.domainConnections.chooseConnection, exact: true })
    ).toBeVisible();
    if (proof) await page.screenshot({ path: `${proof}/dns-wizard-${width}.png`, fullPage: true });
    const metadata = await (await context.request.get(`${api}/observations`)).json();
    expect(metadata.errors).toEqual([]);
    expect(metadata.mutations).toBe(0);
    expect(errors).toEqual([]);
  });
