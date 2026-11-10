import assert from "node:assert/strict";
import { chromium } from "@playwright/test";
const base = process.env.NVKE_STORYBOOK_URL ?? "http://127.0.0.1:64327";
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  for (const name of [
    "install-plan-local-image",
    "install-plan-local-image-read-only",
    "install-plan-local-image-pending",
  ]) {
    await page.goto(
      `${base}/iframe.html?id=screens-nvke-operation--${name}&viewMode=story&globals=mode:light`,
      { timeout: 120000 }
    );
    await page
      .getByRole("heading", { name: "Local image layout", exact: true })
      .waitFor({ timeout: 120000 });
    const inspect = page.getByRole("button", { name: "Review saved image", exact: true });
    assert.equal(await inspect.isEnabled(), name === "install-plan-local-image");
    assert.equal(
      await page.getByRole("button", { name: "Verify local image", exact: true }).isDisabled(),
      true
    );
    const root = page.getByText("sha256:" + "4".repeat(64), { exact: true });
    if (name === "install-plan-local-image-read-only") assert.equal(await root.count(), 0);
    else await root.waitFor();
  }
  assert.deepEqual(errors, []);
  console.log("Qualified three composed operation image states with permission and pending guards");
} finally {
  await browser.close();
}
