import { chromium, expect } from "@playwright/test";
import { createHash } from "node:crypto";
let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
for (const value of [config.storyOrigin, config.endpoint]) {
  const url = new URL(value);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.username || url.password)
    throw new Error("Use owned loopback services");
}
let stage = "launch",
  transfer = null;
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.addInitScript((value) => {
    window.__NVKE_LOCAL_AGENT__ = value;
  }, config);
  let chunks = 0,
    reconciliations = 0,
    pageReads = 0,
    stdout = null,
    reconciled = null;
  await page.route(config.endpoint, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const body = route.request().postDataJSON();
    if (body.operationName === "GetNvkeOperatorArtifactText") chunks++;
    if (body.operationName === "RequestNvkeOperatorJob")
      throw new Error("Original-job inspection must not admit work");
    if (body.operationName === "GetNvkeOperatorJobPage") {
      pageReads++;
      const response = await route.fetch(),
        envelope = (await response.json()).data?.astroliftNvkeOperatorJobPage;
      expect(envelope?.ok).toBe(true);
      stdout = envelope.data.items.find((item) => item.name === "stdout") ?? stdout;
      return route.fulfill({ response });
    }
    if (body.operationName === "ReconcileNvkeOperatorJob") {
      reconciliations++;
      const response = await route.fetch(),
        envelope = (await response.json()).data?.reconcileNvkeOperatorJob;
      expect(envelope?.ok).toBe(true);
      reconciled = envelope.data;
      return route.fulfill({ response });
    }
    return route.continue();
  });
  stage = "open-original-operation";
  await page.goto(
    `${config.storyOrigin}/iframe.html?id=screens-nvke-local-agent-qualification--operation&viewMode=story`
  );
  const load = page.getByRole("button", { name: "Read original receipt", exact: true });
  await expect(load).toBeEnabled({ timeout: 20000 });
  expect(chunks).toBe(0);
  expect(pageReads).toBe(0);
  stage = "explicit-original-receipt";
  await load.click();
  const artifact = page.getByRole("row").filter({ has: page.getByText("stdout", { exact: true }) });
  const download = artifact.getByRole("button", { name: "Download private file", exact: true });
  await expect(download).toBeEnabled({ timeout: 20000 });
  stage = "private-artifact-review";
  await download.click();
  const review = page.getByRole("alertdialog");
  await expect(review).toContainText(config.operationId);
  await expect(review).toContainText(stdout.sha256);
  await expect(review).not.toContainText("browser-private-native-site");
  stage = "hash-checked-private-download";
  [transfer] = await Promise.all([
    page.waitForEvent("download", { timeout: 20000 }),
    review.getByRole("button", { name: "Confirm", exact: true }).click(),
  ]);
  const stream = await transfer.createReadStream();
  expect(stream).toBeTruthy();
  const parts = [];
  for await (const bytes of stream) parts.push(bytes);
  const bytes = Buffer.concat(parts);
  expect(String(bytes.length)).toBe(stdout.size);
  expect(createHash("sha256").update(bytes).digest("hex")).toBe(stdout.sha256);
  expect(bytes.includes(Buffer.from("browser-private-native-site"))).toBe(true);
  expect(transfer.suggestedFilename()).toBe(`nvke-${config.operationId}-stdout`);
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  stage = "reviewed-read-only-reconciliation";
  const reconcile = page.getByRole("button", { name: "Reconcile original job", exact: true });
  await expect(reconcile).toBeEnabled();
  await reconcile.click();
  await expect(page.getByRole("alertdialog")).toContainText("admits no new native process");
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm", exact: true }).click();
  await expect.poll(() => reconciled?.status).toBe("SUCCEEDED");
  await expect(reconcile).not.toBeVisible();
  expect(reconciliations).toBe(1);
  expect(chunks).toBeGreaterThan(0);
  const storage = await page.evaluate(() => Object.values(localStorage).join("\n"));
  expect(storage).not.toContain("browser-private-native-site");
  expect(storage).not.toContain("PRIVATE KEY");
  process.stdout.write(
    JSON.stringify({
      chunks,
      pageReads,
      reconciliations,
      operationId: reconciled.id,
      operationVersion: reconciled.version,
      privateArtifactVerified: true,
      sha256: stdout.sha256,
      size: stdout.size,
    })
  );
} catch (error) {
  process.stderr.write(
    `Owned original-job qualification failed at ${stage} (${error?.name ?? "Error"})\n`
  );
  process.exitCode = 1;
} finally {
  if (transfer) await transfer.delete().catch(() => {});
  await browser.close();
}
