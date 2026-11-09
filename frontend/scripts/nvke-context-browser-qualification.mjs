import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { chromium } from "@playwright/test";

const base = process.env.NVKE_CONTEXT_STORYBOOK ?? "http://127.0.0.1:16418";
const endpoint = new URL(base);
assert(["127.0.0.1", "localhost", "[::1]"].includes(endpoint.hostname));
const output = process.env.NVKE_CONTEXT_BROWSER_OUTPUT;
assert(output, "Select an owned browser qualification output directory");
await mkdir(output, { recursive: true, mode: 0o700 });
const browser = await chromium.launch({ headless: true });
const report = { scope: "controlled-storybook-context-ui", screenshots: [], errors: [], cases: [] };
const url = (id) => `${base}/iframe.html?id=${id}&viewMode=story`;
const contextPrefix = "screens-nvke-install-render-context--";
const labels = {
  artifact: "Artifact UUID",
  item: "Helm item name",
  ordinal: "Original ordinal (from 0)",
  capture: "Capture render context",
  review: "Review saved context",
  retry: "Retry retained capture",
};
async function open(id, width = 1280) {
  const context = await browser.newContext({ viewport: { width, height: 960 } });
  const page = await context.newPage();
  page.on("pageerror", (error) => report.errors.push(error.message));
  await page.goto(url(id), { waitUntil: "domcontentloaded", timeout: 120000 });
  return { context, page };
}
async function screenshot(page, name) {
  const path = `${output}/${name}.png`;
  await page.screenshot({ path, fullPage: true });
  report.screenshots.push(path);
  assert(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    "Horizontal overflow"
  );
}
async function metadata(page) {
  return page.evaluate(() => ({
    calls: window.__NVKE_CONTEXT_FIXTURE_CALLS__ ?? [],
    records: Object.entries(localStorage).filter(([key]) =>
      key.startsWith("astrolift.nvke.install-plan-context.v1:")
    ),
  }));
}
async function fillSource(page) {
  await page.getByRole("button", { name: "New artifact UUID", exact: true }).click();
  const id = await page.getByLabel(labels.artifact, { exact: true }).inputValue();
  await page.getByLabel(labels.item, { exact: true }).fill("nktp");
  await page.getByLabel(labels.ordinal, { exact: true }).fill("1");
  return id;
}
const settingDefaults = {
  "Render revision": "1",
  "Capture byte limit": "16777216",
  "Artifact byte limit": "1048576",
  "Call limit": "512",
  "Iteration limit": "64",
  "Timeout in seconds": "300",
};
try {
  for (const [story, heading, width] of [
    ["full", "Render context", 1280],
    ["unloaded", "Render context", 1280],
    ["width-768", "Render context", 768],
    ["japanese", "レンダリングコンテキスト", 768],
    ["german", "Renderkontext", 768],
  ]) {
    const { context, page } = await open(contextPrefix + story, width);
    await page.getByRole("heading", { name: heading, exact: true }).waitFor({ timeout: 120000 });
    await screenshot(page, story);
    await context.close();
  }
  {
    const { context, page } = await open(
      "screens-nvke-controlled-context-recovery--lost-after-retention"
    );
    await page
      .getByRole("button", { name: labels.capture, exact: true })
      .waitFor({ timeout: 120000 });
    await page.waitForFunction(
      () =>
        !Array.from(document.querySelectorAll("button")).find(
          (b) => b.textContent === "Capture render context"
        )?.disabled
    );
    const id = await fillSource(page);
    await page.getByLabel("Render revision", { exact: true }).fill("9007199254740993");
    await page.getByRole("checkbox", { name: "Render an upgrade", exact: true }).check();
    await page.getByRole("button", { name: labels.capture, exact: true }).click();
    await page
      .getByText("Result unconfirmed. Keep this artifact UUID and review it before retrying.", {
        exact: true,
      })
      .waitFor();
    assert.equal(await page.getByLabel(labels.artifact, { exact: true }).inputValue(), id);
    assert(await page.getByLabel(labels.artifact, { exact: true }).isDisabled());
    let state = await metadata(page);
    assert.equal(state.calls.length, 1);
    assert.equal(state.calls[0].journalBeforeCall, true);
    const intent = JSON.parse(state.records.find(([key]) => key.includes(":intent:"))[1]);
    assert.equal(intent.inputSha256, state.calls[0].inputSha256);
    const stored = JSON.stringify(state.records.map(([, raw]) => JSON.parse(raw)));
    for (const key of [
      "requestText",
      "maximumBytes",
      "revision",
      "upgrade",
      "rendered",
      "credentials",
    ])
      assert(
        !stored.includes(JSON.stringify(key) + ":"),
        "Private capture content retained in browser storage"
      );
    await page.getByRole("button", { name: labels.review, exact: true }).click();
    await page.getByText("3".repeat(64), { exact: true }).waitFor();
    state = await metadata(page);
    assert.equal(state.calls.length, 2);
    assert.equal(state.calls[1].hasPrivateText, false);
    await page.getByRole("button", { name: labels.retry, exact: true }).click();
    await page.waitForFunction(() => window.__NVKE_CONTEXT_FIXTURE_CALLS__?.length === 3);
    await page.getByText("3".repeat(64), { exact: true }).waitFor();
    state = await metadata(page);
    assert.equal(state.calls.filter((c) => c.name === "PrepareNvkeInstallPlanContext").length, 1);
    assert.equal(state.calls[2].hasPrivateText, false);
    await screenshot(page, "lost-after-recovered");
    report.cases.push({ name: "lost-after-retention", calls: state.calls, artifactId: id });
    await context.close();
  }
  {
    const { context, page } = await open(
      "screens-nvke-controlled-context-recovery--lost-before-retention"
    );
    await page
      .getByRole("button", { name: labels.capture, exact: true })
      .waitFor({ timeout: 120000 });
    await page.waitForFunction(
      () =>
        !Array.from(document.querySelectorAll("button")).find(
          (b) => b.textContent === "Capture render context"
        )?.disabled
    );
    const id = await fillSource(page);
    await page.getByRole("button", { name: labels.capture, exact: true }).click();
    await page
      .getByText("Result unconfirmed. Keep this artifact UUID and review it before retrying.", {
        exact: true,
      })
      .waitFor();
    await page.getByRole("button", { name: labels.review, exact: true }).click();
    await page
      .getByText(
        "Use the original settings for this artifact UUID. Its retained private intent cannot be replaced.",
        { exact: true }
      )
      .waitFor();
    for (const [label, value] of Object.entries(settingDefaults))
      await page.getByLabel(label, { exact: true }).fill(value);
    await page.getByLabel("Capture byte limit", { exact: true }).fill("16777217");
    await page.getByRole("button", { name: labels.capture, exact: true }).click();
    await page.waitForFunction(
      () =>
        !Array.from(document.querySelectorAll("button")).find(
          (b) => b.textContent === "Capture render context"
        )?.disabled
    );
    assert.equal((await metadata(page)).calls.length, 2, "Changed settings contacted the server");
    for (const [label, value] of Object.entries(settingDefaults))
      await page.getByLabel(label, { exact: true }).fill(value);
    await page.getByRole("button", { name: labels.capture, exact: true }).click();
    await page.getByText("3".repeat(64), { exact: true }).waitFor();
    const state = await metadata(page);
    const writes = state.calls.filter((c) => c.name === "PrepareNvkeInstallPlanContext");
    assert.equal(writes.length, 2);
    assert.equal(writes[0].inputSha256, writes[1].inputSha256);
    assert(writes.every((c) => c.journalBeforeCall));
    assert.equal(await page.getByLabel(labels.artifact, { exact: true }).inputValue(), id);
    await screenshot(page, "lost-before-exact-reentry");
    report.cases.push({ name: "lost-before-retention", calls: state.calls, artifactId: id });
    await context.close();
  }
  assert.deepEqual(report.errors, []);
  await writeFile(`${output}/report.json`, JSON.stringify(report, null, 2) + "\n", { mode: 0o600 });
  process.stdout.write("Controlled context browser qualification passed\n");
} finally {
  await browser.close();
}
