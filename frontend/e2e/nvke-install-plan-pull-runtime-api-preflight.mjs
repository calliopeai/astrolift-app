import assert from "node:assert/strict";
import { createServer } from "node:http";
import { writeFileSync } from "node:fs";
import { spawn } from "node:child_process";
import { chromium } from "@playwright/test";
const base = process.env.NVKE_PULL_RUNTIME_PREFLIGHT_OUTPUT;
assert(base?.startsWith("/tmp/astrolift-nvke-pull-runtime-"));
const storybook = process.env.NVKE_PULL_RUNTIME_PREFLIGHT_STORYBOOK;
assert(storybook);
const selected = new URL(storybook);
assert(
  selected.protocol === "http:" &&
    selected.hostname === "127.0.0.1" &&
    !selected.username &&
    !selected.password
);
const browser = await chromium.launch({ headless: true });
let fixture, response;
try {
  const page = await browser.newPage();
  await page.goto(
    `${storybook}/iframe.html?id=screens-nvke-pod-node-pull-correspondence--ready&viewMode=story`,
    { timeout: 120000 }
  );
  await page
    .getByRole("heading", { name: "Pod and node pull correspondence", exact: true })
    .waitFor({ timeout: 120000 });
  ({ fixture, response } = await page.evaluate(async () => {
    const panel =
      await import("/components/screens/nvke/NvkeOperatorInstallPlanPullRuntimePanel.stories.tsx");
    const tree =
      await import("/components/screens/nvke/NvkeOperatorInstallPlanTreePanel.stories.tsx");
    const f = panel.INSTALL_PULL_RUNTIME_PROPS;
    return JSON.parse(
      JSON.stringify({
        fixture: {
          scope: {
            server: "http://127.0.0.1:55223/__nvke-pull-runtime-api",
            organizationId: f.site.id,
            actorId: 41,
          },
          source: tree.INSTALL_TREE_PROPS,
          workload: f.workload,
          binding: f.binding,
          pullAuthority: f.pullAuthority,
          analysis: f.analysis,
          nodePlatforms: f.nodePlatforms,
        },
        response: panel.PULL_RUNTIME_RESULT,
      })
    );
  }));
} finally {
  await browser.close();
}
fixture.scope.server = new URL("/__nvke-pull-runtime-api", storybook).href;
let calls = 0,
  failure = false;
const server = createServer(async (req, res) => {
  try {
    assert.equal(req.method, "POST");
    assert.equal(req.headers.authorization, "Bearer owned-noncredential-preflight");
    assert.equal(req.headers["x-astrolift-organization"], fixture.scope.organizationId);
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw);
    assert.equal(body.operationName, "ReviewNvkeInstallPlanPullRuntime");
    assert.equal(body.variables.input.analysisDigest, fixture.analysis.authorityDigest);
    assert.equal(body.variables.input.nodePlatformDigest, fixture.nodePlatforms.platformDigest);
    assert.equal(body.variables.input.requestText, undefined);
    calls++;
    res.writeHead(200, { "content-type": "application/json" });
    res.end(
      JSON.stringify({
        data: {
          reviewNvkeOperatorInstallPlanPullRuntime: { ok: true, errors: [], data: response },
        },
      })
    );
  } catch {
    failure = true;
    res.writeHead(503);
    res.end("Controlled preflight refused");
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const port = server.address().port;
writeFileSync(
  `${base}-config.json`,
  JSON.stringify({
    storybook,
    endpoint: `http://127.0.0.1:${port}/graphql`,
    fixture,
    bearer: "owned-noncredential-preflight",
  }),
  { mode: 0o600 }
);
let code;
try {
  code = await new Promise((resolve) => {
    const child = spawn(process.execPath, ["e2e/nvke-install-plan-pull-runtime-api-runner.mjs"], {
      stdio: "inherit",
      env: { ...process.env, NVKE_PULL_RUNTIME_BROWSER_CONFIG: `${base}-config.json` },
    });
    child.on("exit", resolve);
  });
} finally {
  await new Promise((resolve) => server.close(resolve));
}
assert.equal(code, 0);
assert.equal(calls, 2);
assert.equal(failure, false);
console.log(
  JSON.stringify({ controlledHttp: true, forwardedReadCount: calls, actualAppOrNativeProof: false })
);
