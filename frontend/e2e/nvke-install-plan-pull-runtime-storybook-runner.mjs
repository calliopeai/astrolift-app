import assert from "node:assert/strict";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { chromium } from "@playwright/test";

const base = process.env.NVKE_PULL_RUNTIME_STORYBOOK;
assert(base, "Select the owned pull correspondence Storybook endpoint");
const endpoint = new URL(base);
assert(
  endpoint.protocol === "http:" &&
    endpoint.hostname === "127.0.0.1" &&
    !endpoint.username &&
    !endpoint.password
);
const directory = process.env.NVKE_PULL_RUNTIME_SCREENSHOTS;
assert(directory?.startsWith("/tmp/astrolift-nvke-pull-runtime-"));
mkdirSync(directory, { recursive: true });
const messages = (locale) =>
  JSON.parse(readFileSync(new URL(`../messages/${locale}.json`, import.meta.url), "utf8")).nvke
    .installPullRuntime;
const digest = "a".repeat(64);
const rows = [
  ["ready", "en"],
  ["matched", "en"],
  ["parents-required", "en"],
  ["loading", "en"],
  ["pending", "en"],
  ["read-only", "en"],
  ["unconfirmed", "en"],
  ["stale-analysis", "en"],
  ["stale-node-capture", "en"],
  ["stale-operation", "en"],
  ["replaced-authority", "en"],
  ["lost-reply", "en"],
  ["narrow", "en"],
  ["english", "en"],
  ["spanish", "es"],
  ["german", "de"],
  ["french", "fr"],
  ["portuguese", "pt-BR"],
  ["japanese", "ja"],
  ["korean", "ko"],
  ["chinese", "zh-Hans"],
];
const browser = await chromium.launch({ headless: true });
const results = [],
  errors = [];
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on("pageerror", () => errors.push("page-error"));
  const open = async (story, locale, mode = "light") => {
    const url = new URL("/iframe.html", base);
    url.searchParams.set("id", `screens-nvke-pod-node-pull-correspondence--${story}`);
    url.searchParams.set("viewMode", "story");
    url.searchParams.set("globals", `mode:${mode}`);
    await page.goto(url.href, { timeout: 120000 });
    await page
      .getByRole("heading", { name: messages(locale).title, exact: true })
      .waitFor({ timeout: 120000 });
    await page.waitForFunction(
      (dark) => document.documentElement.classList.contains("dark") === dark,
      mode === "dark"
    );
    assert.equal(errors.length, 0, `${story}: page error`);
    assert.equal(await page.getByRole("button").count(), 1);
    await page.getByText(messages(locale).closureRequired, { exact: true }).waitFor();
  };
  for (const [story, locale] of rows) {
    await open(story, locale);
    const disabled = [
      "parents-required",
      "loading",
      "pending",
      "read-only",
      "stale-operation",
      "replaced-authority",
    ].includes(story);
    assert.equal(await page.getByRole("button").isDisabled(), disabled, story);
    const matched = [
      "matched",
      "narrow",
      "english",
      "spanish",
      "german",
      "french",
      "portuguese",
      "japanese",
      "korean",
      "chinese",
    ].includes(story);
    assert.equal(await page.getByText(digest, { exact: true }).count(), matched ? 1 : 0, story);
    if (story === "lost-reply") {
      await page.getByRole("button", { name: messages(locale).review, exact: true }).click();
      await page
        .getByRole("alert")
        .filter({ hasText: messages(locale).unconfirmed })
        .waitFor();
      assert(await page.getByRole("button").isEnabled());
    }
    results.push({ story, locale, disabled, matched });
  }
  for (const mode of ["light", "dark"])
    for (const width of [375, 768]) {
      await page.setViewportSize({ width, height: 1100 });
      await open("matched", "en", mode);
      assert(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
        `${mode}/${width}: overflow`
      );
      const path = `${directory}/${mode}-${width}.png`;
      await page.screenshot({ path, fullPage: true });
      results.push({ mode, width, screenshot: path });
    }
  for (const story of [
    "install-pull-runtime",
    "install-pull-runtime-read-only",
    "install-pull-runtime-pending",
    "install-pull-runtime-stale-source",
  ]) {
    for (const width of [1440, 375]) {
      await page.setViewportSize({ width, height: 1100 });
      const url = new URL("/iframe.html", base);
      url.searchParams.set("id", `screens-nvke-operation--${story}`);
      url.searchParams.set("viewMode", "story");
      await page.goto(url.href, { timeout: 120000 });
      const heading = page.getByRole("heading", { name: messages("en").title, exact: true });
      await heading.waitFor({ timeout: 120000 });
      const region = page.locator("section").filter({ has: heading });
      assert.equal(
        await region.getByRole("button").isDisabled(),
        story !== "install-pull-runtime",
        story
      );
      assert.equal(
        await region.getByText(digest, { exact: true }).count(),
        story === "install-pull-runtime" ? 1 : 0
      );
      assert(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
        `${story}/${width}: overflow`
      );
      assert.equal(errors.length, 0);
      await region.screenshot({ path: `${directory}/${story}-${width}.png` });
      results.push({ composition: story, width });
    }
  }
} finally {
  await browser.close();
}
assert.equal(errors.length, 0);
writeFileSync(`${directory}/report.json`, JSON.stringify({ results, errors }, null, 2) + "\n");
console.log(
  `Qualified ${rows.length} states/locales, four responsive theme renders and eight operation compositions`
);
