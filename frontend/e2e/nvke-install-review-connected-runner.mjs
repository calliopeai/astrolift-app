import { chromium, expect } from "@playwright/test";
let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
for (const value of [config.storyOrigin, config.endpoint]) {
  const url = new URL(value);
  if (
    url.protocol !== "http:" ||
    url.hostname !== "127.0.0.1" ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  )
    throw new Error("Use owned loopback services");
}
let stage = "launch";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 960 } });
let reviews = 0,
  staleRefused = false,
  prepared = null,
  latestPage = null,
  routeFailed = false;
const pages = [];
try {
  await page.addInitScript((value) => {
    window.__NVKE_LOCAL_AGENT__ = value;
  }, config);
  await page.route(config.endpoint, async (route) => {
    try {
      const cors = {
        "access-control-allow-origin": config.storyOrigin,
        "access-control-allow-methods": "POST, OPTIONS",
        "access-control-allow-headers": "authorization, content-type, x-astrolift-organization",
      };
      if (route.request().method() === "OPTIONS")
        return await route.fulfill({ status: 204, headers: cors });
      expect(route.request().method()).toBe("POST");
      const body = route.request().postDataJSON();
      const isReview = body.operationName === "ReviewNvkeOperatorInstall";
      if (/\bmutation\b/.test(body.query) && !isReview)
        throw new Error("Review must not admit infrastructure work");
      if (isReview) {
        reviews++;
        expect(body.variables.input.operationId).toBe(config.operationId);
        expect(body.variables.input.expectedActorId).toBe(config.actorId);
      }
      const stale = isReview && config.mode === "stale_review" && reviews === 1;
      const response = await route.fetch({
        maxRedirects: 0,
        timeout: 120000,
        ...(stale
          ? {
              postData: {
                ...body,
                variables: {
                  input: {
                    ...body.variables.input,
                    expectedOperationVersion: body.variables.input.expectedOperationVersion + 1,
                  },
                },
              },
            }
          : {}),
      });
      const reply = await response.json();
      if (isReview) {
        const envelope = reply.data?.reviewNvkeOperatorInstall;
        if (stale) {
          expect(envelope?.ok).toBe(false);
          expect(envelope.errors.some((error) => error.code === "VERSION_MISMATCH")).toBe(true);
          staleRefused = true;
        } else {
          expect(envelope?.ok).toBe(true);
          prepared = envelope.data;
          expect(prepared.operationId).toBe(config.operationId);
          expect(prepared.continuationAuthorized).toBe(false);
          expect(prepared.nativeExitCode).not.toBe(0);
          expect(prepared.apiReady).toBe(true);
          expect(prepared.workloadCount).toBeGreaterThan(10);
        }
      }
      if (body.operationName === "GetNvkeOperatorInstallWorkloadPage") {
        const envelope = reply.data?.astroliftNvkeOperatorInstallWorkloadPage;
        expect(envelope?.ok).toBe(true);
        latestPage = envelope.data;
        expect(latestPage.snapshotId).toBe(prepared.snapshotId);
        expect(latestPage.reviewDigest).toBe(prepared.reviewDigest);
        expect(latestPage.operationId).toBe(config.operationId);
        expect(latestPage.operationVersion).toBe(prepared.operationVersion);
        pages.push({ variables: body.variables, page: latestPage });
      }
      await route.fulfill({ response, headers: { ...response.headers(), ...cors } });
    } catch {
      routeFailed = true;
      await route.abort("failed").catch(() => {});
    }
  });
  stage = "load-original-source";
  await page.goto(
    `${config.storyOrigin}/iframe.html?id=screens-nvke-local-agent-qualification--operation&viewMode=story&globals=a11y.manual:!true`
  );
  const panel = page
    .locator("section")
    .filter({
      has: page.getByRole("heading", { name: "Retained installation review", exact: true }),
    });
  const prepare = panel.getByRole("button", { name: "Prepare review", exact: true });
  await expect(prepare).toBeEnabled({ timeout: 30000 });
  expect(reviews).toBe(0);
  expect(pages).toHaveLength(0);
  stage = "explicit-audited-review";
  await prepare.click();
  if (config.mode === "stale_review") {
    await expect.poll(() => staleRefused, { timeout: 30000 }).toBe(true);
    await expect(panel.getByRole("alert")).toBeVisible();
    expect(pages).toHaveLength(0);
    await panel.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(prepare).toBeEnabled({ timeout: 30000 });
    await prepare.click();
  }
  await expect.poll(() => !!prepared, { timeout: 120000 }).toBe(true);
  await expect(panel.getByText(prepared.reviewDigest, { exact: true })).toBeVisible();
  await expect(panel.getByText(/The original install remains uncertain/)).toBeVisible();
  await expect(panel.getByRole("button", { name: /continue/i })).toHaveCount(0);
  stage = "server-paged-snapshot";
  await panel.getByRole("combobox", { name: "Rows per page", exact: true }).click();
  await page.getByRole("option", { name: "10", exact: true }).click();
  await expect.poll(() => pages.at(-1)?.variables.limit).toBe(10);
  const seen = new Set();
  while (true) {
    const current = latestPage;
    await expect(panel.getByRole("row").nth(1)).toContainText(current.items[0].name);
    for (const row of current.items) {
      const key = JSON.stringify([row.namespace, row.kind, row.name]);
      expect(seen.has(key)).toBe(false);
      seen.add(key);
      for (const field of [
        "generation",
        "observedGeneration",
        "desiredReplicas",
        "readyReplicas",
        "updatedReplicas",
      ])
        expect(row[field] === null || /^(0|[1-9][0-9]*)$/.test(row[field])).toBe(true);
    }
    if (!current.nextCursor) break;
    const count = pages.length;
    await panel.getByRole("button", { name: "Older", exact: true }).click();
    await expect.poll(() => pages.length).toBeGreaterThan(count);
    expect(pages.at(-1).variables.after).toBe(current.nextCursor);
  }
  expect(seen.size).toBe(prepared.workloadCount);
  await expect(panel.getByRole("button", { name: "Older", exact: true })).toBeDisabled();
  stage = "server-search";
  await panel.getByRole("searchbox").fill("lgtm");
  await expect.poll(() => pages.at(-1)?.variables.search).toBe("lgtm");
  await expect(panel.getByRole("row").nth(1)).toContainText("lgtm");
  expect(
    latestPage.items.every((row) =>
      [row.namespace, row.kind, row.name, row.observation].join(" ").toLowerCase().includes("lgtm")
    )
  ).toBe(true);
  expect(reviews).toBe(config.mode === "stale_review" ? 2 : 1);
  expect(routeFailed).toBe(false);
  const storage = await page.evaluate(() =>
    [...Object.values(localStorage), ...Object.values(sessionStorage)].join("\n")
  );
  expect(storage).not.toContain(config.token);
  expect(storage).not.toContain("PRIVATE KEY");
  if (config.screenshotPath)
    await page.screenshot({ path: config.screenshotPath, fullPage: true, animations: "disabled" });
  process.stdout.write(
    JSON.stringify({
      mode: config.mode ?? "confirmed",
      reviews,
      staleRefused,
      pages: pages.length,
      workloads: seen.size,
      searchMatches: latestPage.totalCount,
      snapshotId: prepared.snapshotId,
      reviewDigest: prepared.reviewDigest,
      originalVersion: prepared.operationVersion,
      continuationAuthorized: false,
    })
  );
} catch (error) {
  process.stderr.write(
    `Owned install-review browser qualification failed at ${stage} (${error?.name ?? "Error"})\n`
  );
  process.exitCode = 1;
} finally {
  await page.unrouteAll({ behavior: "wait" }).catch(() => {});
  await browser.close();
}
