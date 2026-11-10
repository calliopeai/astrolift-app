import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, lstatSync } from "node:fs";
import { chromium } from "@playwright/test";
const configPath = process.env.NVKE_IMAGE_BINDING_BROWSER_CONFIG;
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
  ...messages.installImageBinding,
};
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  await context.addInitScript((fixture) => {
    window.__NVKE_IMAGE_BINDING_API_FIXTURE__ = fixture;
  }, cfg.fixture);
  const page = await context.newPage(),
    calls = [],
    errors = [];
  page.on("pageerror", () => errors.push("page-error"));
  await page.route("**/__nvke-image-binding-api", async (route) => {
    try {
      const payload = route.request().postDataJSON();
      assert(
        [
          "PrepareNvkeInstallPlanImageBinding",
          "ReviewNvkeInstallPlanImageBinding",
          "RetryNvkeInstallPlanImageBinding",
        ].includes(payload.operationName)
      );
      const source = cfg.fixture.source;
      const expected = {
        workloadId: cfg.fixture.workload?.id ?? null,
        expectedWorkloadVersion: cfg.fixture.workload?.version ?? null,
        workloadDigest: cfg.fixture.workload?.workloadDigest ?? null,
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
      if (payload.operationName === "PrepareNvkeInstallPlanImageBinding") {
        const body = JSON.parse(text);
        assert.deepEqual(body.imageBindings, {
          [cfg.reference]: {
            sourceKind: "registry",
            artifactId: cfg.image.id,
            artifactDigest: cfg.image.artifactDigest,
          },
        });
        assert.equal(body.workloadId, cfg.fixture.workload?.id);
        assert.equal(body.workloadDigest, cfg.fixture.workload?.workloadDigest);
      } else assert.equal(text, undefined);
      const journalBeforeCall = await page.evaluate(
        (artifactId) =>
          Object.values(localStorage).some((value) => {
            try {
              const row = JSON.parse(value);
              return (
                row.kind === "nvke-install-plan-image-binding" &&
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
    `${cfg.storybook}/iframe.html?id=screens-nvke-owned-api-image-binding-recovery--recovery&viewMode=story&globals=mode:light`,
    { timeout: 120000 }
  );
  const prepare = page.getByRole("button", { name: copy.prepare, exact: true });
  await prepare.waitFor({ timeout: 120000 });
  await page.getByLabel(copy.artifactId, { exact: true }).fill(cfg.artifactId);
  await page.getByLabel(copy.maximumBytes, { exact: true }).fill("16777216");
  await page.getByLabel(copy.imageBindings, { exact: true }).fill(
    JSON.stringify({
      [cfg.reference]: {
        sourceKind: "registry",
        artifactId: cfg.image.id,
        artifactDigest: cfg.image.artifactDigest,
      },
    })
  );
  await page.getByLabel(copy.maximumImageBytes, { exact: true }).fill("268435456");
  await prepare.click();
  await page.getByText(copy.failed, { exact: true }).waitFor({ timeout: 360000 });
  assert.equal(calls.filter((c) => c.name === "PrepareNvkeInstallPlanImageBinding").length, 1);
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
  const rootTerm = page.locator("dt", { hasText: copy.bindingDigest });
  const rootDigest = await rootTerm.locator("+ dd").innerText();
  assert.match(rootDigest, /^[0-9a-f]{64}$/);
  const observedTerm = page.locator("dt", { hasText: copy.observedAt });
  const observedAt = await observedTerm.locator("+ dd").innerText();
  const retryResponse = page.waitForResponse(
    (response) => {
      try {
        return (
          response.request().postDataJSON().operationName === "RetryNvkeInstallPlanImageBinding"
        );
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
      "PrepareNvkeInstallPlanImageBinding",
      "ReviewNvkeInstallPlanImageBinding",
      "RetryNvkeInstallPlanImageBinding",
    ]
  );
  assert(calls.slice(1).every((c) => !c.hasPrivateText));
  const storage = await page.evaluate(() => Object.values(localStorage).join("\n"));
  assert(
    !storage.includes(cfg.bearer) &&
      !storage.includes(cfg.reference) &&
      !storage.includes(cfg.image.id) &&
      !storage.includes("imageBindings") &&
      !storage.includes('"policy"') &&
      !storage.includes("requestText") &&
      !storage.includes("maximumBytes") &&
      !storage.includes("maximumCalls") &&
      !storage.includes("timeoutSeconds")
  );
  const journal = await page.evaluate(
    (artifactId) =>
      Object.values(localStorage)
        .map((value) => JSON.parse(value))
        .find(
          (row) => row.kind === "nvke-install-plan-image-binding" && row.artifactId === artifactId
        ),
    cfg.artifactId
  );
  assert.equal(journal.inputSha256, calls[0].inputSha256);
  assert.equal(journal.workloadId, cfg.fixture.workload?.id);
  assert.equal(journal.workloadVersion, cfg.fixture.workload?.version);
  assert.equal(journal.workloadDigest, cfg.fixture.workload?.workloadDigest);
  assert.equal(await page.getByRole("button", { name: /execute|continue|resume/i }).count(), 0);
  assert.deepEqual(errors, []);
  console.log(
    JSON.stringify({
      passed: true,
      workloadId: cfg.fixture.workload?.id,
      workloadVersion: cfg.fixture.workload?.version,
      workloadDigest: cfg.fixture.workload?.workloadDigest,
      calls: calls.map((c) => c.name),
      artifactDigest: digest,
      captureDigest: capture,
      bindingDigest: rootDigest,
      observedAt,
      pageErrors: errors,
    })
  );
  await context.close();
} finally {
  await browser.close();
}
