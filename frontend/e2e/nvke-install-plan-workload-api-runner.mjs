import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, lstatSync } from "node:fs";
import { chromium } from "@playwright/test";
const configPath = process.env.NVKE_WORKLOAD_BROWSER_CONFIG;
assert(configPath, "Select the owned private browser qualification config");
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
const messages = JSON.parse(
  readFileSync(new URL("../messages/en.json", import.meta.url), "utf8")
).nvke;
const copy = {
  ...messages.installTree,
  ...messages.installContext,
  ...messages.installWorkload,
};
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  await context.addInitScript((fixture) => {
    window.__NVKE_WORKLOAD_API_FIXTURE__ = fixture;
  }, cfg.fixture);
  const page = await context.newPage(),
    calls = [],
    errors = [];
  page.on("pageerror", () => errors.push("page-error"));
  await page.route("**/__nvke-workload-api", async (route) => {
    try {
      const payload = route.request().postDataJSON();
      assert(
        [
          "PrepareNvkeInstallPlanWorkload",
          "ReviewNvkeInstallPlanWorkload",
          "RetryNvkeInstallPlanWorkload",
        ].includes(payload.operationName)
      );
      const source = cfg.fixture.source;
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
      };
      for (const [key, value] of Object.entries(expected))
        assert.equal(payload.variables.input[key], value);
      const text = payload.variables.input.requestText;
      if (payload.operationName === "PrepareNvkeInstallPlanWorkload") {
        assert.equal(typeof text, "string");
        const body = JSON.parse(text);
        assert.deepEqual(body.contextBindings, cfg.contextBindings);
        assert.equal(body.artifactId, cfg.artifactId);
        assert.deepEqual(
          Object.keys(body).sort(),
          [
            "format",
            "artifactId",
            "planId",
            "planDigest",
            "treeId",
            "treeDigest",
            "contextBindings",
            "maximumResources",
            "maximumBytes",
            "timeoutSeconds",
          ].sort()
        );
      } else {
        assert.equal(text, undefined);
      }
      const journalBeforeCall = await page.evaluate(
        (artifactId) =>
          Object.values(localStorage).some((value) => {
            try {
              const row = JSON.parse(value);
              return (
                row.kind === "nvke-install-plan-workload" &&
                row.artifactId === artifactId &&
                typeof row.inputSha256 === "string"
              );
            } catch {
              return false;
            }
          }),
        cfg.artifactId
      );
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
        headers: {
          "content-type": "application/json",
          Authorization: `Bearer ${cfg.bearer}`,
          "X-Astrolift-Organization": cfg.fixture.scope.organizationId,
        },
        timeout: 360000,
      });
      await route.fulfill({ response });
    } catch {
      errors.push("API-forwarding-failed");
      await route.fulfill({ status: 503, body: "Owned qualification transport unavailable" });
    }
  });
  await page.goto(
    `${cfg.storybook}/iframe.html?id=screens-nvke-owned-api-workload-recovery--recovery&viewMode=story&globals=mode:light`,
    { timeout: 120000 }
  );
  const prepare = page.getByRole("button", { name: copy.prepare, exact: true });
  await prepare.waitFor({ timeout: 120000 });
  await page.getByLabel(copy.artifactId, { exact: true }).fill(cfg.artifactId);
  await page.getByLabel(copy.maximumBytes, { exact: true }).fill("16777216");
  await page
    .getByLabel(copy.contextBindings, { exact: true })
    .fill(JSON.stringify(cfg.contextBindings));
  await prepare.click();
  await page.getByText(copy.failed, { exact: true }).waitFor({ timeout: 360000 });
  assert.equal(calls.filter((c) => c.name === "PrepareNvkeInstallPlanWorkload").length, 1);
  assert.equal(calls[0].journalBeforeCall, true);
  assert.equal(await prepare.isDisabled(), true);
  assert.equal(
    await page.getByLabel(copy.artifactId, { exact: true }).inputValue(),
    cfg.artifactId
  );
  await page.reload({ waitUntil: "domcontentloaded", timeout: 120000 });
  await prepare.waitFor({ timeout: 120000 });
  assert.equal(await prepare.isDisabled(), true);
  assert.equal(
    await page.getByLabel(copy.artifactId, { exact: true }).inputValue(),
    cfg.artifactId
  );
  assert.equal(calls.length, 1);
  await page.waitForFunction(
    (id) =>
      [...document.querySelectorAll("input")].some((input) => input.disabled && input.value === id),
    cfg.artifactId
  );
  await page.getByRole("button", { name: copy.review, exact: true }).click();
  const digestTerm = page.locator("dt", { hasText: copy.artifactDigest });
  await digestTerm.waitFor({ timeout: 360000 });
  const digest = await digestTerm.locator("+ dd").innerText();
  assert.match(digest, /^[0-9a-f]{64}$/);
  const captureTerm = page.locator("dt", { hasText: copy.captureDigest });
  const capture = await captureTerm.locator("+ dd").innerText();
  assert.match(capture, /^[0-9a-f]{64}$/);
  const rootTerm = page.locator("dt", { hasText: copy.workloadDigest });
  const rootDigest = await rootTerm.locator("+ dd").innerText();
  assert.match(rootDigest, /^[0-9a-f]{64}$/);
  const observedTerm = page.locator("dt", { hasText: copy.observedAt });
  const observedAt = await observedTerm.locator("+ dd").innerText();
  const retryResponse = page.waitForResponse(
    (response) => {
      try {
        return response.request().postDataJSON().operationName === "RetryNvkeInstallPlanWorkload";
      } catch {
        return false;
      }
    },
    { timeout: 360000 }
  );
  await page.getByRole("button", { name: copy.retry, exact: true }).click();
  await retryResponse;
  await digestTerm.waitFor({ timeout: 360000 });
  assert.equal(await digestTerm.locator("+ dd").innerText(), digest);
  assert.equal(await captureTerm.locator("+ dd").innerText(), capture);
  assert.equal(await rootTerm.locator("+ dd").innerText(), rootDigest);
  assert.equal(await observedTerm.locator("+ dd").innerText(), observedAt);
  assert.deepEqual(
    calls.map((c) => c.name),
    [
      "PrepareNvkeInstallPlanWorkload",
      "ReviewNvkeInstallPlanWorkload",
      "RetryNvkeInstallPlanWorkload",
    ]
  );
  assert(calls.slice(1).every((c) => !c.hasPrivateText));
  const storage = await page.evaluate(() => Object.values(localStorage).join("\n"));
  assert(
    !storage.includes(cfg.bearer) &&
      !Object.values(cfg.contextBindings).some((row) => storage.includes(row.contextId)) &&
      !storage.includes('"contextBindings"') &&
      !storage.includes("requestText") &&
      !storage.includes("maximumBytes") &&
      !storage.includes("maximumResources") &&
      !storage.includes("timeoutSeconds")
  );
  const journal = await page.evaluate(
    (artifactId) =>
      Object.values(localStorage)
        .map((value) => JSON.parse(value))
        .find((row) => row.kind === "nvke-install-plan-workload" && row.artifactId === artifactId),
    cfg.artifactId
  );
  assert.equal(journal.inputSha256, calls[0].inputSha256);
  assert.equal(await page.getByRole("button", { name: /execute|continue|resume/i }).count(), 0);
  assert.deepEqual(errors, []);
  console.log(
    JSON.stringify({
      passed: true,
      kind: "workload",
      calls: calls.map((c) => c.name),
      artifactDigest: digest,
      captureDigest: capture,
      workloadDigest: rootDigest,
      observedAt,
      pageErrors: errors,
    })
  );
  await context.close();
} finally {
  await browser.close();
}
