import assert from "node:assert/strict";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { chromium } from "@playwright/test";

const base = process.env.NVKE_NODE_PLATFORMS_STORYBOOK;
assert(base, "Select the owned node/platform Storybook endpoint");
const endpoint = new URL(base);
assert(
  endpoint.protocol === "http:" &&
    endpoint.hostname === "127.0.0.1" &&
    !endpoint.username &&
    !endpoint.password
);
const directory = process.env.NVKE_NODE_PLATFORMS_SCREENSHOTS;
assert(directory?.startsWith("/tmp/astrolift-nvke-node-platform-"));
mkdirSync(directory, { recursive: true });
const messages = (locale) =>
  JSON.parse(readFileSync(new URL(`../messages/${locale}.json`, import.meta.url), "utf8")).nvke;
const english = messages("en"),
  common = english.installImmutable,
  copy = english.installNodePlatforms;
const rows = [
  ["observed", "en"],
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
    url.searchParams.set("id", `screens-nvke-node-platform-observations--${story}`);
    url.searchParams.set("viewMode", "story");
    url.searchParams.set("globals", `mode:${mode}`);
    if (form) url.searchParams.set("args", "artifacts:!null;retainedArtifactId:!null");
    await page.goto(url.href, { timeout: 120000 });
    await page
      .getByRole("heading", { name: messages(locale).installNodePlatforms.title, exact: true })
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
      ["parent-required", "loading", "pending", "read-only", "stale-source", "unverified"].includes(
        story
      )
    ) {
      for (const action of [common.prepare, common.review, common.retry])
        assert(await page.getByRole("button", { name: action, exact: true }).isDisabled());
    }
    if (
      [
        "parent-required",
        "loading",
        "lost-reply",
        "read-only",
        "stale-source",
        "unverified",
        "stale-binding",
      ].includes(story)
    ) {
      assert.equal(await page.getByText("7".repeat(64), { exact: true }).count(), 0);
    }
    if (story === "observed") {
      assert(await page.getByRole("button", { name: common.prepare, exact: true }).isDisabled());
      assert(await page.getByText("7".repeat(64), { exact: true }).count());
      await page.screenshot({ path: `${directory}/observed-light.png`, fullPage: true });
    }
    if (story === "prepare-lost-reply") {
      await page.getByRole("button", { name: english.installTree.newId, exact: true }).click();
      const id = await page
        .getByLabel(english.installTree.artifactId, { exact: true })
        .inputValue();
      const privateScope = JSON.stringify({
        "private-node": {
          uid: "private-uid",
          runtimeProfile: "containerd/v2.3.0",
          platform: { os: "linux", architecture: "arm64" },
        },
      });
      await page.getByLabel(copy.nodePlatforms, { exact: true }).fill(privateScope);
      await page.getByRole("button", { name: common.prepare, exact: true }).click();
      await page.getByText(english.installTree.unconfirmed, { exact: true }).waitFor();
      assert.equal(
        await page.getByLabel(english.installTree.artifactId, { exact: true }).inputValue(),
        id
      );
      assert.equal(await page.getByLabel(copy.nodePlatforms, { exact: true }).count(), 0);
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
    await open("observed", "en", mode);
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
    const localCopy = messages(locale).installNodePlatforms;
    await page.getByLabel(localCopy.nodePlatforms, { exact: true }).waitFor();
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
  assert.equal(errors.length, 0);
  writeFileSync(
    `${directory}/results.json`,
    JSON.stringify({ stories: results, locales: 8, darkModes: 2, pageErrors: errors }, null, 2) +
      "\n"
  );
  console.log(
    JSON.stringify({ stories: results.length, locales: 8, darkModes: 2, pageErrors: errors.length })
  );
} finally {
  await browser.close();
}
