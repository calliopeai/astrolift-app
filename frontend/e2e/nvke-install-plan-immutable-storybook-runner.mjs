import assert from "node:assert/strict";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { chromium } from "@playwright/test";

const base = process.env.NVKE_IMMUTABLE_STORYBOOK;
assert(base, "Select the owned immutable-artifact Storybook endpoint");
const endpoint = new URL(base);
assert(
  endpoint.protocol === "http:" &&
    endpoint.hostname === "127.0.0.1" &&
    !endpoint.username &&
    !endpoint.password
);
const directory = process.env.NVKE_IMMUTABLE_SCREENSHOTS;
assert(directory?.startsWith("/tmp/astrolift-nvke-immutable-"));
mkdirSync(directory, { recursive: true });
const messages = (locale) =>
  JSON.parse(readFileSync(new URL(`../messages/${locale}.json`, import.meta.url), "utf8")).nvke;
const english = messages("en"),
  text = english.installImmutable;
const rows = [
  ["payload", "en"],
  ["execution", "en"],
  ["unloaded", "en"],
  ["unloaded-execution", "en"],
  ["parent-required", "en"],
  ["loading", "en"],
  ["pending", "en"],
  ["lost-reply", "en"],
  ["other-reviewer", "en"],
  ["read-only", "en"],
  ["stale-binding", "en"],
  ["stale-source", "en"],
  ["unverified", "en"],
  ["original-preparation-recovery", "en"],
  ["narrow", "en"],
  ["choose-another-artifact", "en"],
  ["german", "de"],
  ["spanish", "es"],
  ["french", "fr"],
  ["portuguese", "pt-BR"],
  ["japanese", "ja"],
  ["korean", "ko"],
  ["chinese", "zh-Hans"],
];
const browser = await chromium.launch({ headless: true });
const results = [];
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const errors = [];
  page.on("pageerror", () => errors.push("page-error"));
  for (const [name, locale] of rows) {
    const copy = messages(locale).installImmutable;
    await page.goto(
      `${base}/iframe.html?id=screens-nvke-immutable-install-artifacts--${name}&viewMode=story&globals=mode:light`,
      { timeout: 120000 }
    );
    await page
      .getByText(
        ["execution", "unloaded-execution"].includes(name)
          ? copy.titleExecution
          : copy.titlePayload,
        { exact: true }
      )
      .waitFor({ timeout: 120000 });
    assert.equal(await page.getByRole("button", { name: /execute|continue|resume/i }).count(), 0);
    if (
      ["parent-required", "loading", "pending", "read-only", "stale-source", "unverified"].includes(
        name
      )
    ) {
      for (const action of [text.prepare, text.review, text.retry])
        assert(await page.getByRole("button", { name: action, exact: true }).isDisabled());
    }
    if (
      [
        "parent-required",
        "loading",
        "read-only",
        "stale-source",
        "unverified",
        "stale-binding",
        "lost-reply",
      ].includes(name)
    ) {
      assert.equal(await page.getByText("1".repeat(64), { exact: true }).count(), 0);
    }
    if (["payload", "execution"].includes(name)) {
      assert(await page.getByRole("button", { name: text.prepare, exact: true }).isDisabled());
      await page.getByText("1".repeat(64), { exact: true }).waitFor();
      await page.screenshot({ path: `${directory}/${name}-light.png`, fullPage: true });
      await page.setViewportSize({ width: 768, height: 1100 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 768);
      await page.screenshot({ path: `${directory}/${name}-768.png`, fullPage: true });
      await page.setViewportSize({ width: 1440, height: 1100 });
    }
    if (name === "unloaded") {
      await page.getByRole("button", { name: english.installTree.newId, exact: true }).click();
      const id = await page
        .getByLabel(english.installTree.artifactId, { exact: true })
        .inputValue();
      assert.match(id, /^[0-9a-f-]{36}$/);
      await page.getByLabel(text.item, { exact: true }).fill("nktp");
      await page.getByRole("button", { name: text.prepare, exact: true }).click();
      await page.waitForFunction(
        () => document.querySelector('input[id$="-artifactId"]')?.disabled === true
      );
      assert.equal(
        await page.getByLabel(english.installTree.artifactId, { exact: true }).inputValue(),
        id
      );
      assert.equal(await page.getByLabel(text.item, { exact: true }).count(), 0);
    }
    if (locale !== "en") {
      await page.setViewportSize({ width: 375, height: 1100 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 375);
      await page.setViewportSize({ width: 1440, height: 1100 });
    }
    assert.equal(errors.length, 0, `${name}: page error`);
    results.push({ story: name, locale });
  }
  for (const name of ["payload", "execution"]) {
    await page.goto(
      `${base}/iframe.html?id=screens-nvke-immutable-install-artifacts--${name}&viewMode=story&globals=mode:dark`,
      { timeout: 120000 }
    );
    await page
      .getByText(name === "payload" ? text.titlePayload : text.titleExecution, { exact: true })
      .waitFor({ timeout: 120000 });
    await page.waitForFunction(() => document.documentElement.classList.contains("dark"));
    await page.screenshot({ path: `${directory}/${name}-dark.png`, fullPage: true });
  }
  writeFileSync(
    `${directory}/results.json`,
    JSON.stringify({ stories: results, pageErrors: errors, darkModes: 2 }, null, 2) + "\n"
  );
  console.log(
    JSON.stringify({ stories: results.length, locales: 8, darkModes: 2, pageErrors: errors.length })
  );
} finally {
  await browser.close();
}
