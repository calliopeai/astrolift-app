import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, lstatSync } from "node:fs";
import { chromium, expect } from "@playwright/test";

const configPath = process.env.NVKE_NODE_PLATFORMS_BROWSER_CONFIG;
assert(configPath, "Select the owned private node-platform browser config");
const stat = lstatSync(configPath);
assert(
  stat.isFile() &&
    !stat.isSymbolicLink() &&
    (stat.mode & 0o777) === 0o600 &&
    stat.nlink === 1 &&
    stat.uid === process.getuid()
);
const cfg = JSON.parse(readFileSync(configPath, "utf8"));
for (const endpoint of [cfg.storybook, cfg.endpoint]) {
  const url = new URL(endpoint);
  assert(
    url.protocol === "http:" && url.hostname === "127.0.0.1" && !url.username && !url.password
  );
}
assert.equal(cfg.body.artifactId, cfg.artifactId);
const operations = ["Prepare", "Review", "Retry"].map(
  (action) => action + "NvkeInstallPlanNodePlatforms"
);
const kind = "nvke-install-plan-node-platforms";
const messages = JSON.parse(
  readFileSync(new URL("../messages/en.json", import.meta.url), "utf8")
).nvke;
const copy = {
  ...messages.installTree,
  ...messages.installContext,
  ...messages.installImmutable,
  ...messages.installNodePlatforms,
};
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  await context.addInitScript((fixture) => {
    window.__NVKE_NODE_PLATFORMS_API_FIXTURE__ = fixture;
  }, cfg.fixture);
  const page = await context.newPage(),
    calls = [],
    errors = [];
  page.on("pageerror", () => errors.push("page-error"));
  await page.route("**/__nvke-node-platforms-api", async (route) => {
    try {
      const payload = route.request().postDataJSON();
      assert(operations.includes(payload.operationName));
      const source = cfg.fixture.source,
        workload = cfg.fixture.workload,
        binding = cfg.fixture.binding;
      const expected = {
        operationId: source.operation.id,
        expectedActorId: cfg.fixture.scope.actorId,
        expectedSiteVersion: source.site.version,
        expectedOperationVersion: source.operation.version,
        expectedAuthorityVersion: source.authority.version,
        planId: source.plan.id,
        expectedPlanVersion: source.plan.version,
        planDigest: source.plan.plan.planDigest,
        chartArtifactId: source.charts.id,
        expectedChartArtifactVersion: source.charts.version,
        chartArtifactDigest: source.charts.artifactDigest,
        treeId: source.artifacts.id,
        expectedTreeVersion: source.artifacts.version,
        treeDigest: source.artifacts.treeDigest,
        workloadId: workload.id,
        expectedWorkloadVersion: workload.version,
        workloadDigest: workload.workloadDigest,
        bindingId: binding.id,
        expectedBindingVersion: binding.version,
        bindingDigest: binding.bindingDigest,
      };
      const input = payload.variables.input,
        text = input.requestText;
      for (const [key, value] of Object.entries(expected)) assert.equal(input[key], value);
      const preparing = payload.operationName === operations[0];
      assert.deepEqual(
        Object.keys(input).sort(),
        [...Object.keys(expected), preparing ? "requestText" : "artifactId"].sort()
      );
      if (preparing) assert.deepEqual(JSON.parse(text), cfg.body);
      else {
        assert.equal(text, undefined);
        assert.equal(input.artifactId, cfg.artifactId);
      }
      const journalBeforeCall = await page.evaluate(
        ({ artifactId, kind }) =>
          Object.values(localStorage).some((value) => {
            try {
              const row = JSON.parse(value);
              return (
                row.kind === kind &&
                row.artifactId === artifactId &&
                typeof row.inputSha256 === "string"
              );
            } catch {
              return false;
            }
          }),
        { artifactId: cfg.artifactId, kind }
      );
      assert(journalBeforeCall);
      calls.push({
        name: payload.operationName,
        hasPrivateText: typeof text === "string",
        journalBeforeCall,
        ...(typeof text === "string"
          ? { inputSha256: createHash("sha256").update(text).digest("hex") }
          : {}),
      });
      const response = await route.fetch({
        url: cfg.endpoint,
        maxRedirects: 0,
        maxRetries: 0,
        timeout: 360000,
        headers: {
          "content-type": "application/json",
          Authorization: `Bearer ${cfg.bearer}`,
          "X-Astrolift-Organization": cfg.fixture.scope.organizationId,
        },
      });
      await route.fulfill({ response });
    } catch {
      errors.push("API-forwarding-failed");
      await route.fulfill({ status: 503, body: "Owned qualification transport unavailable" });
    }
  });
  await page.goto(
    `${cfg.storybook}/iframe.html?id=screens-nvke-owned-api-node-platform-recovery--recovery&viewMode=story&globals=mode:light`,
    { timeout: 120000 }
  );
  const prepare = page.getByRole("button", { name: copy.prepare, exact: true });
  await prepare.waitFor({ timeout: 120000 });
  await page.getByLabel(copy.artifactId, { exact: true }).fill(cfg.artifactId);
  await page
    .getByLabel(copy.nodePlatforms, { exact: true })
    .fill(JSON.stringify(cfg.body.nodePlatforms));
  await page.locator("summary", { hasText: copy.limitsTitle }).click();
  for (const key of [
    "maximumImageBytes",
    "maximumMetadataBytes",
    "maximumBytes",
    "maximumObservationBytes",
    "maximumHelperBytes",
    "maximumCalls",
    "maximumPairs",
    "timeoutSeconds",
  ]) {
    await page.getByLabel(copy[key], { exact: true }).fill(String(cfg.body[key]));
  }
  await prepare.click();
  await page.getByText(copy.failed, { exact: true }).waitFor({ timeout: 360000 });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].name, operations[0]);
  assert(await prepare.isDisabled());
  assert.equal(
    await page.getByLabel(copy.artifactId, { exact: true }).inputValue(),
    cfg.artifactId
  );
  assert.equal(await page.getByLabel(copy.nodePlatforms, { exact: true }).count(), 0);
  await page.reload({ waitUntil: "domcontentloaded", timeout: 120000 });
  await prepare.waitFor({ timeout: 120000 });
  await page.waitForFunction(
    (id) =>
      [...document.querySelectorAll("input")].some((input) => input.disabled && input.value === id),
    cfg.artifactId
  );
  assert(await prepare.isDisabled());
  assert.equal(calls.length, 1);
  await page.getByRole("button", { name: copy.review, exact: true }).click();
  const term = (label) =>
    page
      .locator("dt")
      .filter({ hasText: new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`) })
      .locator("+ dd");
  await term(copy.artifactDigest).waitFor({ timeout: 360000 });
  const digest = await term(copy.artifactDigest).innerText();
  const capture = await term(copy.captureDigest).innerText();
  const rawDigest = await term(copy.platformDigest).innerText();
  const observedAt = await term(copy.observedAt).innerText();
  for (const value of [digest, capture, rawDigest]) assert.match(value, /^[0-9a-f]{64}$/);
  const response = page.waitForResponse(
    (response) => {
      try {
        return response.request().postDataJSON().operationName === operations[2];
      } catch {
        return false;
      }
    },
    { timeout: 360000 }
  );
  await page.getByRole("button", { name: copy.retry, exact: true }).click();
  await response;
  await expect(page.getByRole("button", { name: copy.retry, exact: true })).toBeEnabled({
    timeout: 360000,
  });
  await term(copy.artifactDigest).waitFor({ timeout: 360000 });
  assert.equal(await page.getByText(copy.failed, { exact: true }).count(), 0);
  assert.equal(await term(copy.artifactDigest).innerText(), digest);
  assert.equal(await term(copy.captureDigest).innerText(), capture);
  assert.equal(await term(copy.platformDigest).innerText(), rawDigest);
  assert.equal(await term(copy.observedAt).innerText(), observedAt);
  assert.deepEqual(
    calls.map((c) => c.name),
    operations
  );
  assert(calls.slice(1).every((c) => !c.hasPrivateText));
  const records = await page.evaluate(() =>
    Object.values(localStorage).map((value) => JSON.parse(value))
  );
  const journal = records.find((row) => row.kind === kind && row.artifactId === cfg.artifactId);
  assert(journal);
  assert.equal(journal.inputSha256, calls[0].inputSha256);
  assert.equal(journal.mode, "platform");
  assert.equal(journal.bindingId, cfg.fixture.binding.id);
  assert.equal(journal.bindingVersion, cfg.fixture.binding.version);
  assert.equal(journal.bindingDigest, cfg.fixture.binding.bindingDigest);
  for (const field of [
    "nodePlatforms",
    "runtimeProfile",
    "platform",
    "uid",
    "requestText",
    "maximumBytes",
    "maximumResources",
    "timeoutSeconds",
    "credentials",
  ])
    assert(!records.some((row) => Object.hasOwn(row, field)));
  const storage = JSON.stringify(records);
  assert(!storage.includes(cfg.bearer));
  for (const [name, selection] of Object.entries(cfg.body.nodePlatforms)) {
    assert(!storage.includes(name));
    assert(!storage.includes(selection.uid));
    assert(!storage.includes(selection.runtimeProfile));
  }
  assert.equal(await page.getByRole("button", { name: /execute|continue|resume/i }).count(), 0);
  assert.deepEqual(errors, []);
  console.log(
    JSON.stringify({
      passed: true,
      runtimeProfiles: new Set(
        Object.values(cfg.body.nodePlatforms).map((row) => row.runtimeProfile)
      ).size,
      calls: calls.map((c) => c.name),
      artifactDigest: digest,
      captureDigest: capture,
      rawDigest,
      observedAt,
      pageErrors: errors,
    })
  );
  await context.close();
} finally {
  await browser.close();
}
