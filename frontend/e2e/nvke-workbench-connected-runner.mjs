import { chromium, expect } from "@playwright/test";
let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
for (const value of [config.storyOrigin, config.endpoint]) {
  const url = new URL(value);
  if (url.hostname !== "127.0.0.1" || url.protocol !== "http:" || url.username || url.password)
    throw new Error("Use owned loopback qualification services");
}
let stage = "launch";
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.addInitScript((value) => {
    window.__NVKE_LOCAL_AGENT__ = value;
  }, config);
  let admissions = 0,
    receiptReads = 0,
    originalId = null,
    recovered = null;
  await page.route(config.endpoint, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const body = route.request().postDataJSON();
    if (body.operationName === "RequestNvkeOperatorJob") {
      admissions++;
      originalId = body.variables.input.requestId;
      const response = await route.fetch();
      const envelope = (await response.json()).data?.requestNvkeOperatorJob;
      expect(envelope?.ok).toBe(true);
      await route.abort("failed");
      return;
    }
    if (body.operationName === "GetNvkeRequest") {
      receiptReads++;
      expect(body.variables.requestId).toBe(originalId);
      const response = await route.fetch();
      recovered = (await response.json()).data?.astroliftNvkeRequest;
      return route.fulfill({ response });
    }
    return route.continue();
  });
  stage = "open-workbench";
  await page.goto(
    `${config.storyOrigin}/iframe.html?id=screens-nvke-local-agent-qualification--workbench&viewMode=story`
  );
  const reviewButton = page.getByRole("button", { name: "Review change", exact: true });
  await expect(reviewButton).toBeEnabled({ timeout: 20000 });
  stage = "private-generation-request";
  await page.getByRole("combobox").click();
  await page.getByRole("option", { name: "generate.sitespec", exact: true }).click();
  const text =
    JSON.stringify(
      {
        format: 1,
        revision: config.revision,
        siteId: config.siteId,
        command: "generate.sitespec",
        positionals: ["browser-private-native-site"],
        options: { nodes: "input:nodes.csv" },
        files: { "nodes.csv": "name,ip\ncp-1,192.0.2.10\n" },
      },
      null,
      2
    ) + "\n";
  const editor = page.getByLabel("Private request envelope (JSON)", { exact: true });
  await editor.fill(text);
  await reviewButton.click();
  const review = page.getByRole("alertdialog");
  await expect(review).toContainText("generate.sitespec");
  await expect(review).not.toContainText("browser-private-native-site");
  await expect(editor).toHaveValue("");
  stage = "lost-admission-reply";
  await review.getByRole("button", { name: "Submit reviewed change", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible({ timeout: 20000 });
  expect(admissions).toBe(1);
  const before = await page.evaluate(() => Object.values(window.localStorage).join("\n"));
  expect(before).not.toContain("browser-private-native-site");
  expect(before).not.toContain("nodes.csv");
  await expect(editor).toBeDisabled();
  stage = "refresh-original-recovery";
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  const recover = page.getByRole("button", { name: "Look up original receipt", exact: true });
  await expect(recover).toBeEnabled({ timeout: 20000 });
  stage = "lookup-original-receipt";
  await recover.click();
  await expect.poll(() => recovered?.id).toBeTruthy();
  await expect(recover).not.toBeVisible();
  expect(admissions).toBe(1);
  expect(receiptReads).toBe(1);
  const saved = await page.evaluate(() =>
    Object.entries(window.localStorage)
      .filter(([key]) => key.includes(":receipt:"))
      .map(([, value]) => JSON.parse(value))
  );
  expect(saved).toHaveLength(1);
  expect(saved[0].requestId).toBe(originalId);
  expect(saved[0].operationId).toBe(recovered.id);
  expect(JSON.stringify(saved)).not.toContain("browser-private-native-site");
  process.stdout.write(
    JSON.stringify({
      admissions,
      receiptReads,
      operationId: recovered.id,
      requestId: originalId,
      privateDraftCleared: true,
    })
  );
} catch (error) {
  process.stderr.write(
    `Owned workbench qualification failed at ${stage} (${error?.name ?? "Error"})\n`
  );
  process.exitCode = 1;
} finally {
  await browser.close();
}
