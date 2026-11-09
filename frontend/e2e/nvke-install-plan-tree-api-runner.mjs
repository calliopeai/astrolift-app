import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, lstatSync } from "node:fs";
import { chromium } from "@playwright/test";
const configPath = process.env.NVKE_TREE_BROWSER_CONFIG;
assert(configPath, "Select the owned private browser qualification config");
const stat = lstatSync(configPath);
assert(
  stat.isFile() && !stat.isSymbolicLink() && (stat.mode & 0o777) === 0o600 && stat.nlink === 1
);
const cfg = JSON.parse(readFileSync(configPath, "utf8"));
for (const endpoint of [cfg.storybook, cfg.endpoint]) {
  const url = new URL(endpoint);
  assert(
    url.protocol === "http:" && url.hostname === "127.0.0.1" && !url.username && !url.password
  );
}
const copy = JSON.parse(readFileSync(new URL("../messages/en.json", import.meta.url), "utf8")).nvke
  .installTree;
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  await context.addInitScript((fixture) => {
    window.__NVKE_TREE_API_FIXTURE__ = fixture;
  }, cfg.fixture);
  const page = await context.newPage(),
    calls = [],
    errors = [];
  page.on("pageerror", () => errors.push("page-error"));
  await page.route("**/__nvke-tree-api", async (route) => {
    try {
      const payload = route.request().postDataJSON();
      assert(
        [
          "PrepareNvkeInstallPlanTree",
          "ReviewNvkeInstallPlanTree",
          "RetryNvkeInstallPlanTree",
        ].includes(payload.operationName)
      );
      assert.equal(payload.variables.input.chartArtifactId, cfg.fixture.source.artifacts.id);
      assert.equal(
        payload.variables.input.chartArtifactDigest,
        cfg.fixture.source.artifacts.artifactDigest
      );
      assert.equal(payload.variables.input.planId, cfg.fixture.source.plan.id);
      const text = payload.variables.input.requestText;
      const journalBeforeCall = await page.evaluate(
        (artifactId) =>
          Object.values(localStorage).some((value) => {
            try {
              const row = JSON.parse(value);
              return (
                row.kind === "nvke-install-plan-tree" &&
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
    `${cfg.storybook}/iframe.html?id=screens-nvke-owned-api-tree-recovery--recovery&viewMode=story`,
    { timeout: 120000 }
  );
  const prepare = page.getByRole("button", { name: copy.prepare, exact: true });
  await prepare.waitFor({ timeout: 120000 });
  await page.getByLabel(copy.artifactId, { exact: true }).fill(cfg.artifactId);
  await page.getByLabel(copy.treeLimit, { exact: true }).fill("16777216");
  await prepare.click();
  await page.getByText(copy.failed, { exact: true }).waitFor({ timeout: 360000 });
  assert.equal(calls.filter((c) => c.name === "PrepareNvkeInstallPlanTree").length, 1);
  assert.equal(calls[0].journalBeforeCall, true);
  assert.equal(await prepare.isDisabled(), true);
  assert.equal(
    await page.getByLabel(copy.artifactId, { exact: true }).inputValue(),
    cfg.artifactId
  );
  await page.getByRole("button", { name: copy.review, exact: true }).click();
  const digestTerm = page.locator("dt", { hasText: copy.treeDigest });
  await digestTerm.waitFor({ timeout: 360000 });
  const digest = await digestTerm.locator("+ dd").innerText();
  assert.match(digest, /^[0-9a-f]{64}$/);
  const bytesTerm = page.locator("dt", { hasText: copy.treeBytes });
  const bytes = await bytesTerm.locator("+ dd").innerText();
  assert.match(bytes, /^[1-9][0-9]*$/);
  const retryResponse = page.waitForResponse(
    (response) => {
      try {
        return response.request().postDataJSON().operationName === "RetryNvkeInstallPlanTree";
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
  assert.equal(await bytesTerm.locator("+ dd").innerText(), bytes);
  assert.deepEqual(
    calls.map((c) => c.name),
    ["PrepareNvkeInstallPlanTree", "ReviewNvkeInstallPlanTree", "RetryNvkeInstallPlanTree"]
  );
  assert(calls.slice(1).every((c) => !c.hasPrivateText));
  const storage = await page.evaluate(() => Object.values(localStorage).join("\n"));
  assert(
    !storage.includes(cfg.bearer) &&
      !storage.includes("requestText") &&
      !storage.includes("maximumBytes")
  );
  const journal = await page.evaluate(
    (artifactId) =>
      Object.values(localStorage)
        .map((value) => JSON.parse(value))
        .find((row) => row.kind === "nvke-install-plan-tree" && row.artifactId === artifactId),
    cfg.artifactId
  );
  assert.equal(journal.inputSha256, calls[0].inputSha256);
  assert.equal(await page.getByRole("button", { name: /execute|continue|resume/i }).count(), 0);
  assert.deepEqual(errors, []);
  console.log(
    JSON.stringify({
      passed: true,
      calls: calls.map((c) => c.name),
      treeDigest: digest,
      bytes,
      pageErrors: errors,
    })
  );
  await context.close();
} finally {
  await browser.close();
}
