import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import en from "../messages/en.json";
const api = `http://127.0.0.1:${process.env.ROUTE_API_PORT ?? 6172}`,
  c = en.installAlertMail;
for (const width of [1440, 390])
  test(`real settings SMTP own-mailbox review and history at ${width}px`, async ({
    page,
    context,
  }, info) => {
    await page.setViewportSize({ width, height: 1000 });
    await context.request.post(`${api}/observations/reset`);
    await context.addCookies([
      { name: "sessionid", value: "owner", url: info.project.use.baseURL! },
      { name: "backend_jwt", value: "route-fixture-token", url: info.project.use.baseURL! },
    ]);
    await page.routeWebSocket("**/app/gql/config/ws/", (s) =>
      s.onMessage((m) => {
        const f = JSON.parse(String(m));
        if (f.type === "connection_init") s.send(JSON.stringify({ type: "connection_ack" }));
        if (f.type === "ping") s.send(JSON.stringify({ type: "pong" }));
      })
    );
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(m.text());
    });
    if (width === 1440) {
      await page.goto("/domains/55555555-5555-4555-8555-555555555555/email");
      await page.getByRole("link", { name: c.open, exact: true }).click();
      await expect(page).toHaveURL(/\/settings\/notifications\?section=install-email$/);
    } else await page.goto("/settings/notifications?section=install-email");
    const panel = page.getByRole("region", { name: c.title, exact: true });
    await expect(panel.getByRole("button", { name: c.review, exact: true })).toBeEnabled();
    await expect(panel.getByRole("button", { name: c.send, exact: true })).toBeDisabled();
    await expect(panel.getByRole("textbox")).toHaveCount(0);
    await expect(panel.getByText("operator@example.test", { exact: true })).toBeVisible();
    await panel.getByRole("button", { name: c.review, exact: true }).click();
    await expect(panel.getByText(c.reviewed, { exact: true })).toBeVisible();
    if (process.env.ALERT_MAIL_UI_PROOF) {
      await mkdir(process.env.ALERT_MAIL_UI_PROOF, { recursive: true });
      await page.screenshot({
        path: `${process.env.ALERT_MAIL_UI_PROOF}/alert-mail-reviewed-${width}.png`,
        fullPage: true,
      });
    }
    await panel.getByRole("button", { name: c.send, exact: true }).click();
    await expect(panel.getByText(c.acceptance, { exact: true })).toBeVisible();
    await expect(panel.getByRole("button", { name: c.send, exact: true })).toBeDisabled();
    await expect(panel.getByRole("button", { name: c.another, exact: true })).toBeVisible();
    expect(
      await context.request.get(`${api}/observations/alert-mail-writes`).then((r) => r.json())
    ).toHaveLength(1);
    if (width < 768) {
      expect((await page.locator("#main-content").boundingBox())!.width).toBeGreaterThan(360);
      await expect(page.getByRole("navigation", { name: "Main", exact: true })).toHaveCount(0);
    }
    if (process.env.ALERT_MAIL_UI_PROOF)
      await page.screenshot({
        path: `${process.env.ALERT_MAIL_UI_PROOF}/alert-mail-history-${width}.png`,
        fullPage: true,
      });
    const metadata = await context.request.get(`${api}/observations`).then((r) => r.json());
    expect(metadata.errors).toEqual([]);
    expect(metadata.mutations).toBe(0);
    expect(errors).toEqual([]);
  });
test("reader cannot navigate to an enabled install SMTP diagnostic", async ({
  page,
  context,
}, info) => {
  await context.addCookies([
    { name: "sessionid", value: "reader", url: info.project.use.baseURL! },
    { name: "backend_jwt", value: "route-fixture-token", url: info.project.use.baseURL! },
  ]);
  await page.goto("/settings/notifications?section=install-email");
  await expect(page.getByRole("heading", { name: "Notifications", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: c.send, exact: true })).toHaveCount(0);
});
