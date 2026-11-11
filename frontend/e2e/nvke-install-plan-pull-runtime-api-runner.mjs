import assert from "node:assert/strict";
import { readFileSync, lstatSync } from "node:fs";
import { chromium, expect } from "@playwright/test";

const path = process.env.NVKE_PULL_RUNTIME_BROWSER_CONFIG;
assert(path, "Select the owned private correspondence browser config");
const stat = lstatSync(path);
assert(
  stat.isFile() &&
    !stat.isSymbolicLink() &&
    (stat.mode & 0o777) === 0o600 &&
    stat.nlink === 1 &&
    stat.uid === process.getuid()
);
const cfg = JSON.parse(readFileSync(path, "utf8"));
for (const endpoint of [cfg.storybook, cfg.endpoint]) {
  const url = new URL(endpoint);
  assert(
    url.protocol === "http:" && url.hostname === "127.0.0.1" && !url.username && !url.password
  );
}
const f = cfg.fixture,
  source = f.source;
const expected = {
  operationId: source.operation.id,
  expectedActorId: f.scope.actorId,
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
  workloadId: f.workload.id,
  expectedWorkloadVersion: f.workload.version,
  workloadDigest: f.workload.workloadDigest,
  bindingId: f.binding.id,
  expectedBindingVersion: f.binding.version,
  bindingDigest: f.binding.bindingDigest,
  pullAuthorityId: f.pullAuthority.id,
  expectedPullAuthorityVersion: f.pullAuthority.version,
  pullAuthorityDigest: f.pullAuthority.authorityDigest,
  analysisId: f.analysis.id,
  expectedAnalysisVersion: f.analysis.version,
  analysisDigest: f.analysis.authorityDigest,
  nodePlatformId: f.nodePlatforms.id,
  expectedNodePlatformVersion: f.nodePlatforms.version,
  nodePlatformDigest: f.nodePlatforms.platformDigest,
};
const copy = JSON.parse(readFileSync(new URL("../messages/en.json", import.meta.url), "utf8")).nvke
  .installPullRuntime;
const browser = await chromium.launch({ headless: true });
const calls = [],
  errors = [],
  accepted = [];
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  await context.addInitScript((fixture) => {
    window.__NVKE_PULL_RUNTIME_API_FIXTURE__ = fixture;
  }, f);
  const page = await context.newPage();
  page.on("pageerror", () => errors.push("page-error"));
  await page.route("**/__nvke-pull-runtime-api", async (route) => {
    try {
      const payload = route.request().postDataJSON();
      assert.equal(payload.operationName, "ReviewNvkeInstallPlanPullRuntime");
      assert.deepEqual(payload.variables.input, expected);
      calls.push({ operation: payload.operationName, dropped: calls.length === 0 });
      const response = await route.fetch({
        url: cfg.endpoint,
        maxRedirects: 0,
        maxRetries: 0,
        timeout: 360000,
        headers: {
          "content-type": "application/json",
          Authorization: `Bearer ${cfg.bearer}`,
          "X-Astrolift-Organization": f.scope.organizationId,
        },
      });
      assert.equal(response.status(), 200);
      const body = await response.json();
      assert.equal(body.errors, undefined);
      const result = body.data?.reviewNvkeOperatorInstallPlanPullRuntime;
      assert.equal(result?.ok, true);
      assert.equal(result.data.continuationAuthorized, false);
      assert.match(result.data.correspondenceDigest, /^[0-9a-f]{64}$/);
      assert.equal(result.data.analysis.id, f.analysis.id);
      assert.equal(result.data.analysis.version, f.analysis.version);
      assert.equal(result.data.analysis.authorityDigest, f.analysis.authorityDigest);
      assert.equal(result.data.nodePlatforms.id, f.nodePlatforms.id);
      assert.equal(result.data.nodePlatforms.version, f.nodePlatforms.version);
      assert.equal(result.data.nodePlatforms.platformDigest, f.nodePlatforms.platformDigest);
      accepted.push(result.data.correspondenceDigest);
      if (calls.length === 1)
        await route.fulfill({ status: 503, body: "Owned deliberately lost read response" });
      else await route.fulfill({ response });
    } catch {
      errors.push("API-forwarding-failed");
      await route.fulfill({ status: 503, body: "Owned qualification transport unavailable" });
    }
  });
  await page.goto(
    `${cfg.storybook}/iframe.html?id=screens-nvke-owned-api-pod-node-correspondence--review&viewMode=story&globals=mode:light`,
    { timeout: 120000 }
  );
  const review = () => page.getByRole("button", { name: copy.review, exact: true });
  await review().waitFor({ timeout: 120000 });
  assert.equal(calls.length, 0);
  await review().click();
  await page.getByRole("alert").filter({ hasText: copy.unconfirmed }).waitFor({ timeout: 360000 });
  assert.equal(calls.length, 1);
  assert.equal(errors.length, 0);
  await page.reload({ waitUntil: "domcontentloaded", timeout: 120000 });
  await review().waitFor({ timeout: 120000 });
  assert.equal(calls.length, 1);
  await review().click();
  const digest = page.locator("dt").filter({ hasText: copy.digest }).locator("+ dd");
  await digest.waitFor({ timeout: 360000 });
  assert.equal(await digest.innerText(), accepted[0]);
  assert.equal(calls.length, 2);
  assert.equal(accepted[1], accepted[0]);
  await expect(review()).toBeEnabled();
  const records = await page.evaluate(() => ({
    local: { ...localStorage },
    session: { ...sessionStorage },
  }));
  assert.equal(JSON.stringify(records).includes(accepted[0]), false);
  assert.equal(JSON.stringify(records).includes(cfg.bearer), false);
  await page.reload({ waitUntil: "domcontentloaded", timeout: 120000 });
  await review().waitFor({ timeout: 120000 });
  assert.equal(await digest.count(), 0);
  assert.equal(calls.length, 2);
  assert.equal(errors.length, 0);
  console.log(
    JSON.stringify({
      readCount: calls.length,
      deliberatelyLostReplies: 1,
      stableCorrespondenceDigest: true,
      noAutomaticReadOrRetry: true,
      noResultOrCredentialsInStorage: true,
      errors,
    })
  );
} finally {
  await browser.close();
}
