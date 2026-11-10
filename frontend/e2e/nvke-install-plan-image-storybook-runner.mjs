import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium } from "@playwright/test";

const base = process.env.NVKE_STORYBOOK_URL ?? "http://127.0.0.1:64327";
const artifact = "2be02f88-dc9b-47fb-9662-96504efde439";
const rootDigest = "sha256:" + "4".repeat(64);
const cases = [
  "full",
  "unloaded",
  "parent-required",
  "loading",
  "pending",
  "lost-reply",
  "other-reviewer",
  "read-only",
  "stale-parent",
  "stale-tree",
  "stale-source",
  "unverified",
  "original-preparation-recovery",
  "long-digest",
  "narrow",
  "choose-another-artifact",
];
const browser = await chromium.launch({ headless: true });
const results = [];
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  for (const name of cases) {
    await page.goto(
      `${base}/iframe.html?id=screens-nvke-install-registry-image--${name}&viewMode=story&globals=mode:light`,
      { timeout: 120000 }
    );
    await page.getByText("Registry image", { exact: true }).waitFor({ timeout: 120000 });
    assert.equal(await page.getByRole("button", { name: /execute|continue|resume/i }).count(), 0);
    if (
      [
        "read-only",
        "parent-required",
        "stale-source",
        "unverified",
        "stale-parent",
        "stale-tree",
        "loading",
        "lost-reply",
      ].includes(name)
    )
      assert.equal(await page.getByText(rootDigest, { exact: true }).count(), 0);
    if (
      [
        "read-only",
        "parent-required",
        "stale-source",
        "unverified",
        "stale-parent",
        "loading",
        "pending",
      ].includes(name)
    )
      assert.equal(
        await page.getByRole("button", { name: "Review saved image", exact: true }).isDisabled(),
        true
      );
    if (name === "full") {
      await page.waitForFunction(() => !document.documentElement.classList.contains("dark"));
      await page.getByText(rootDigest, { exact: true }).waitFor();
      assert.equal(await page.getByText("67108864", { exact: true }).count(), 1);
      assert.equal(
        await page.getByRole("button", { name: "Capture image", exact: true }).isDisabled(),
        true
      );
      await page.screenshot({ path: "/tmp/astrolift-nvke-image-ui-full.png", fullPage: true });
      await page.setViewportSize({ width: 768, height: 1100 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 768);
      await page.screenshot({ path: "/tmp/astrolift-nvke-image-ui-768.png", fullPage: true });
      await page.setViewportSize({ width: 1440, height: 1100 });
    }
    if (name === "unloaded") {
      await page.getByRole("button", { name: "New artifact UUID", exact: true }).click();
      const id = await page.getByLabel("Artifact UUID", { exact: true }).inputValue();
      assert.match(id, /^[0-9a-f-]{36}$/);
      await page
        .getByLabel("Image reference", { exact: true })
        .fill("private.example.test/owned:v1");
      await page.getByLabel("Reviewed policy alias", { exact: true }).fill("reviewed");
      await page.getByRole("button", { name: "Capture image", exact: true }).click();
      await page.getByRole("button", { name: "Capture image", exact: true }).waitFor();
      await page.waitForFunction(
        () => document.querySelector('input[id$="-artifactId"]')?.disabled === true
      );
      assert.equal(await page.getByLabel("Artifact UUID", { exact: true }).inputValue(), id);
      assert.equal(await page.getByLabel("Image reference", { exact: true }).count(), 0);
    }
    if (name === "lost-reply") {
      assert.equal(await page.getByLabel("Artifact UUID", { exact: true }).inputValue(), artifact);
      assert.equal(
        await page.getByRole("button", { name: "Review saved image", exact: true }).isEnabled(),
        true
      );
    }
    if (name === "other-reviewer")
      assert.equal(
        await page
          .getByRole("button", { name: "Retry retained image capture", exact: true })
          .isDisabled(),
        true
      );
    if (name === "long-digest") {
      await page.setViewportSize({ width: 768, height: 1100 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 768);
      await page.getByText("sha512:" + "4".repeat(128), { exact: true }).waitFor();
      await page.setViewportSize({ width: 1440, height: 1100 });
    }
    results.push(name);
  }
  for (const [name, locale] of Object.entries({
    german: "de",
    spanish: "es",
    french: "fr",
    portuguese: "pt-BR",
    japanese: "ja",
    korean: "ko",
    chinese: "zh-Hans",
  })) {
    const messages = JSON.parse(
      readFileSync(new URL(`../messages/${locale}.json`, import.meta.url), "utf8")
    );
    await page.goto(
      `${base}/iframe.html?id=screens-nvke-install-registry-image--${name}&viewMode=story&globals=mode:light`,
      { timeout: 120000 }
    );
    await page
      .getByText(messages.nvke.installImage.title, { exact: true })
      .waitFor({ timeout: 120000 });
    await page.getByText(rootDigest, { exact: true }).waitFor();
    results.push(locale);
  }
  await page.goto(
    `${base}/iframe.html?id=screens-nvke-install-registry-image--full&viewMode=story&globals=mode:dark`,
    { timeout: 120000 }
  );
  await page.getByText(rootDigest, { exact: true }).waitFor({ timeout: 120000 });
  await page.waitForFunction(() => document.documentElement.classList.contains("dark"));
  await page.screenshot({ path: "/tmp/astrolift-nvke-image-ui-dark.png", fullPage: true });
  assert.deepEqual(errors, []);
  writeFileSync(
    "/tmp/astrolift-nvke-image-storybook-result.json",
    JSON.stringify(
      { cases: results, pageErrors: 0, responsiveWidths: [1440, 768], themes: ["light", "dark"] },
      null,
      2
    )
  );
  console.log(
    `Qualified ${results.length} image stories, responsive layouts and light/dark themes`
  );
} finally {
  await browser.close();
}
