// `npm run smoke:live:login`: a headed browser opens the fleet page; sign in
// as usual. Once the console is back on /fleet with a session cookie, the
// browser state is saved to LIVE_STORAGE_STATE (mode 0600) for the smoke.
import { chmodSync } from "node:fs";
import { expect, test } from "@playwright/test";

test("sign in and save the session", async ({ page, context, baseURL }) => {
  const origin = new URL(baseURL!).origin;
  await page.goto("/fleet");
  await expect
    .poll(
      async () => {
        const url = new URL(page.url());
        const cookies = await context.cookies(origin);
        return (
          url.origin === origin &&
          url.pathname.startsWith("/fleet") &&
          cookies.some((cookie) => cookie.name === "backend_jwt")
        );
      },
      { timeout: 870_000, intervals: [2_000] }
    )
    .toBe(true);
  const path = process.env.LIVE_STORAGE_STATE!;
  await context.storageState({ path });
  chmodSync(path, 0o600);
});
