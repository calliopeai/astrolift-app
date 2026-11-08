import { chromium, expect } from "@playwright/test";
let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
for (const value of [config.storyOrigin, config.endpoint]) {
  const url = new URL(value);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.username || url.password)
    throw new Error("Use owned loopback services");
}
let stage = "launch";
const browser = await chromium.launch();
const page = await browser.newPage();
try {
  await page.addInitScript((value) => {
    window.__NVKE_LOCAL_AGENT__ = value;
  }, config);
  let stops = 0,
    pages = 0,
    settled = null,
    stopResult = null,
    routeFailed = false;
  await page.route(config.endpoint, async (route) => {
    try {
      if (route.request().method() !== "POST") return await route.continue();
      const body = route.request().postDataJSON();
      if (body.operationName === "RequestNvkeOperatorJob")
        throw new Error("Stop must not admit work");
      if (body.operationName === "GetNvkeOperatorJobPage") {
        pages++;
        const response = await route.fetch({ maxRedirects: 0 });
        const envelope = (await response.json()).data?.astroliftNvkeOperatorJobPage;
        expect(envelope?.ok).toBe(true);
        if (envelope.data.errorCode === "ORIGINAL_JOB_FENCED") settled = envelope.data;
        return await route.fulfill({ response });
      }
      if (body.operationName === "StopNvkeOperatorJob") {
        stops++;
        expect(body.variables.input.operationId).toBe(config.operationId);
        expect(body.variables.input.expectedActorId).toBe(config.actorId);
        const response =
          config.mode === "stale_review"
            ? await route.fetch({
                maxRedirects: 0,
                postData: {
                  ...body,
                  variables: {
                    input: {
                      ...body.variables.input,
                      expectedOperationVersion: body.variables.input.expectedOperationVersion + 1,
                    },
                  },
                },
              })
            : await route.fetch({ maxRedirects: 0 });
        stopResult = (await response.json()).data?.stopNvkeOperatorJob;
        if (config.mode === "lost_reply")
          return await route.fulfill({ status: 503, body: "Original stop reply unconfirmed" });
        return await route.fulfill({ response });
      }
      return await route.continue();
    } catch {
      routeFailed = true;
      await route.abort("failed").catch(() => {});
    }
  });
  stage = "original-operation";
  await page.goto(
    `${config.storyOrigin}/iframe.html?id=screens-nvke-local-agent-qualification--operation&viewMode=story`
  );
  const panel = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "Original native job", exact: true }) });
  const load = panel.getByRole("button", { name: "Read original receipt", exact: true });
  await expect(load).toBeEnabled({ timeout: 20000 });
  expect(pages).toBe(0);
  expect(stops).toBe(0);
  stage = "explicit-receipt-review";
  await load.click();
  const stop = panel.getByRole("button", { name: "Stop or fence original job", exact: true });
  await expect(stop).toBeEnabled({ timeout: 20000 });
  await expect(panel).toContainText("not_found");
  await stop.click();
  const review = page.getByRole("alertdialog");
  await expect(review).toContainText(config.operationId);
  await expect(review).toContainText("tooling.bootstrap.site");
  await expect(review).toContainText("Remote effects can remain uncertain");
  expect(stops).toBe(0);
  stage = "reviewed-stop";
  await review.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect.poll(() => stops).toBe(1);
  await expect.poll(() => stopResult?.ok).toBe(config.mode !== "stale_review");
  if (config.mode !== "confirmed") {
    await expect(panel.getByRole("alert")).toBeVisible();
    await expect(stop).toBeDisabled();
    await expect(
      panel.getByRole("button", { name: "Reconcile original job", exact: true })
    ).toBeDisabled();
    stage = "explicit-refresh-after-unconfirmed-reply";
    await panel.getByRole("button", { name: "Refresh", exact: true }).click();
  }
  if (config.mode === "stale_review") {
    expect(stopResult?.ok).toBe(false);
    expect(stopResult.errors.some((error) => error.code === "VERSION_MISMATCH")).toBe(true);
    await expect(load).toBeEnabled({ timeout: 20000 });
    await load.click();
    await expect(stop).toBeEnabled({ timeout: 20000 });
    await expect(panel).toContainText("not_found");
  } else {
    expect(stopResult?.ok).toBe(true);
    expect(stopResult.data.id).toBe(config.operationId);
    expect(stopResult.data.status).toBe("CANCELLED");
    await expect(load).toBeEnabled({ timeout: 20000 });
    await load.click();
    await expect.poll(() => settled?.errorCode).toBe("ORIGINAL_JOB_FENCED");
    expect(settled.state).toBe("cancelled");
    expect(settled.effectful).toBe(true);
    await expect(stop).toHaveCount(0);
  }
  expect(stops).toBe(1);
  expect(routeFailed).toBe(false);
  const storage = await page.evaluate(() =>
    [...Object.values(localStorage), ...Object.values(sessionStorage)].join("\n")
  );
  expect(storage).not.toContain(config.token);
  expect(storage).not.toContain("PRIVATE KEY");
  process.stdout.write(
    JSON.stringify({
      stops,
      pages,
      operationId: config.operationId,
      mode: config.mode,
      fenced: !!settled,
      staleRefused: config.mode === "stale_review",
    })
  );
} catch (error) {
  process.stderr.write(
    `Owned original-stop qualification failed at ${stage} (${error?.name ?? "Error"})\n`
  );
  process.exitCode = 1;
} finally {
  await page.unrouteAll({ behavior: "wait" }).catch(() => {});
  await browser.close();
}
