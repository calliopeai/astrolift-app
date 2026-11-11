import assert from "node:assert/strict";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { chromium } from "@playwright/test";

const base = process.env.NVKE_PULL_SECRET_ANALYSIS_STORYBOOK;
assert(base, "Select the owned Pod pull Storybook endpoint");
const endpoint = new URL(base);
assert(
  endpoint.protocol === "http:" &&
    endpoint.hostname === "127.0.0.1" &&
    !endpoint.username &&
    !endpoint.password
);
const directory = process.env.NVKE_PULL_SECRET_ANALYSIS_SCREENSHOTS;
assert(directory?.startsWith("/tmp/astrolift-nvke-pull-secret-analysis-"));
mkdirSync(directory, { recursive: true });
const messages = (locale) =>
  JSON.parse(readFileSync(new URL(`../messages/${locale}.json`, import.meta.url), "utf8")).nvke;
const english = messages("en"),
  common = english.installImmutable,
  copy = english.installPullSecretAnalysis;
const rows = [
  ["interpreted", "en"],
  ["no-secret-candidates", "en"],
  ["pull-observation-required", "en"],
  ["stale-pull-observation", "en"],
  ["unloaded", "en"],
  ["prepare-lost-reply", "en"],
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
  ["choose-another-artifact", "en"],
  ["narrow", "en"],
  ["german", "de"],
  ["spanish", "es"],
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
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  page.on("pageerror", () => errors.push("page-error"));
  const open = async (story, locale, mode = "light", form = false) => {
    const url = new URL("/iframe.html", base);
    url.searchParams.set("id", `screens-nvke-image-credential-candidates--${story}`);
    url.searchParams.set("viewMode", "story");
    url.searchParams.set("globals", `mode:${mode}`);
    if (form) url.searchParams.set("args", "artifacts:!null;retainedArtifactId:!null");
    await page.goto(url.href, { timeout: 120000 });
    await page
      .getByRole("heading", { name: messages(locale).installPullSecretAnalysis.title, exact: true })
      .waitFor({ timeout: 120000 });
    await page.waitForFunction(
      (dark) => document.documentElement.classList.contains("dark") === dark,
      mode === "dark"
    );
    assert.equal(errors.length, 0, `${story}: page error`);
  };
  for (const [story, locale] of rows) {
    await open(story, locale);
    assert.equal(
      await page.getByRole("button", { name: /execute|continue|resume|install now/i }).count(),
      0
    );
    if (
      [
        "parent-required",
        "pull-observation-required",
        "stale-binding",
        "loading",
        "pending",
        "read-only",
        "stale-source",
        "unverified",
      ].includes(story)
    ) {
      for (const action of [common.prepare, common.review, common.retry])
        assert(await page.getByRole("button", { name: action, exact: true }).isDisabled());
    }
    if (
      [
        "parent-required",
        "pull-observation-required",
        "stale-pull-observation",
        "loading",
        "lost-reply",
        "read-only",
        "stale-source",
        "unverified",
        "stale-binding",
      ].includes(story)
    ) {
      assert.equal(await page.getByText("8".repeat(64), { exact: true }).count(), 0);
    }
    if (story === "interpreted") {
      assert(await page.getByRole("button", { name: common.prepare, exact: true }).isDisabled());
      assert(await page.getByText("8".repeat(64), { exact: true }).count());
      await page.screenshot({ path: `${directory}/observed-light.png`, fullPage: true });
    }
    if (story === "prepare-lost-reply") {
      await page.getByRole("button", { name: english.installTree.newId, exact: true }).click();
      const id = await page
        .getByLabel(english.installTree.artifactId, { exact: true })
        .inputValue();
      const privateScope = JSON.stringify([
        {
          podKey: "private-namespace/private-pod",
          imagePointer: "/spec/containers/0/image",
          ensureSecretPulledImages: true,
        },
      ]);
      await page.getByLabel(copy.imageSelections, { exact: true }).fill(privateScope);
      await page.getByRole("button", { name: common.prepare, exact: true }).click();
      await page.getByText(english.installTree.unconfirmed, { exact: true }).waitFor();
      assert.equal(
        await page.getByLabel(english.installTree.artifactId, { exact: true }).inputValue(),
        id
      );
      assert.equal(await page.getByLabel(copy.imageSelections, { exact: true }).count(), 0);
      assert(await page.getByRole("button", { name: common.prepare, exact: true }).isDisabled());
      await page.screenshot({ path: `${directory}/lost-reply.png`, fullPage: true });
      await page.getByRole("button", { name: common.review, exact: true }).click();
      await page
        .getByText(english.installTree.unconfirmed, { exact: true })
        .waitFor({ state: "hidden" });
    }
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth),
      1440,
      `${story}: horizontal overflow`
    );
    results.push({ story, locale });
  }
  for (const mode of ["light", "dark"]) {
    await open("interpreted", "en", mode);
    for (const width of [768, 375]) {
      await page.setViewportSize({ width, height: 1100 });
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth),
        width,
        `${mode}/${width}: overflow`
      );
      await page.screenshot({ path: `${directory}/observed-${mode}-${width}.png`, fullPage: true });
    }
    await page.setViewportSize({ width: 1440, height: 1100 });
  }
  for (const [story, locale] of [
    ["unloaded", "en"],
    ...rows.filter(([, locale]) => locale !== "en"),
  ]) {
    await open(story, locale, "light", true);
    const localCopy = messages(locale).installPullSecretAnalysis;
    await page.getByLabel(localCopy.imageSelections, { exact: true }).waitFor();
    await page.locator("summary", { hasText: localCopy.limitsTitle }).click();
    await page.setViewportSize({ width: 375, height: 1100 });
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth),
      375,
      `${locale}: form overflow`
    );
    await page.screenshot({ path: `${directory}/form-${locale}-375.png`, fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1100 });
  }
  const compositions = [];
  for (const story of [
    "install-pull-secret-analysis",
    "install-pull-secret-analysis-read-only",
    "install-pull-secret-analysis-pending",
  ]) {
    for (const width of [1440, 375]) {
      await page.setViewportSize({ width, height: 1100 });
      const url = new URL("/iframe.html", base);
      url.searchParams.set("id", `screens-nvke-operation--${story}`);
      url.searchParams.set("viewMode", "story");
      await page.goto(url.href, { timeout: 120000 });
      const heading = page.getByRole("heading", { name: copy.title, exact: true });
      await heading.waitFor({ timeout: 120000 });
      const region = page.locator("section").filter({ has: heading });
      for (const action of [common.prepare, common.review, common.retry]) {
        const button = region.getByRole("button", { name: action, exact: true });
        if (story !== "install-pull-secret-analysis") assert(await button.isDisabled());
      }
      if (story === "install-pull-secret-analysis") {
        assert(await region.getByText("8".repeat(64), { exact: true }).count());
        assert(await region.getByRole("button", { name: common.review, exact: true }).isEnabled());
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), width);
      assert.equal(errors.length, 0);
      await page.screenshot({ path: `${directory}/${story}-${width}.png`, fullPage: true });
      await region.screenshot({ path: `${directory}/${story}-panel-${width}.png` });
      compositions.push({ story, width });
    }
  }
  writeFileSync(
    `${directory}/results.json`,
    JSON.stringify(
      { stories: results, compositions, locales: 8, darkModes: 2, pageErrors: errors },
      null,
      2
    ) + "\n"
  );
  console.log(
    JSON.stringify({
      stories: results.length,
      compositions: compositions.length,
      locales: 8,
      darkModes: 2,
      pageErrors: errors.length,
    })
  );
} finally {
  await browser.close();
}
