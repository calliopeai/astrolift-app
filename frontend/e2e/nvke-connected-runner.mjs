import { chromium, expect } from "@playwright/test";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const story = new URL(config.storyOrigin);
const endpoint = new URL(config.endpoint);
if (
  [story, endpoint].some(
    (url) =>
      url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.username || url.password
  )
)
  throw new Error("Qualification requires owned loopback services");
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.addInitScript((value) => {
    window.__NVKE_LOCAL_QUALIFICATION__ = value;
  }, config);
  let requests = 0;
  let requestId;
  await page.route(config.endpoint, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const body = route.request().postDataJSON();
    if (body.operationName !== "RequestNvkeNativeReset") return route.continue();
    requests++;
    requestId = body.variables.input.requestId;
    const response = await route.fetch();
    const result = await response.json();
    expect(result.data?.requestNvkeNativeReset?.ok).toBe(true);
    // The API has accepted the durable intent; the browser never receives that reply.
    await route.abort("failed");
  });
  await page.goto(
    `${story.origin}/iframe.html?id=screens-nvke-local-qualification--connected&viewMode=story`
  );
  const inspect = page.getByRole("button", {
    name: "Inspect RotateEtcdEncryptionKey",
    exact: true,
  });
  await expect(inspect).toBeEnabled({ timeout: 20000 });
  await inspect.click();
  const row = page
    .getByRole("row")
    .filter({ has: page.getByText("StartEtcdEncryptionRewrite", { exact: true }) })
    .first();
  const select = row.getByRole("button", { name: "Select for reset", exact: true });
  await expect(select).toBeEnabled({ timeout: 20000 });
  await select.click();
  const sheet = page.getByRole("dialog");
  await sheet
    .getByLabel("Reset rationale (private)", { exact: true })
    .fill("PRIVATE_BROWSER_RESET_REASON");
  await sheet
    .getByLabel("Conflicting node-work evidence reference (private)", { exact: true })
    .fill("PRIVATE_BROWSER_QUIESCENCE_EVIDENCE");
  await sheet.getByRole("combobox").click();
  await page.getByRole("option", { name: "restart_rewrite", exact: true }).click();
  await sheet
    .getByRole("checkbox", {
      name: "I reviewed this exact native phase, run and event boundary.",
      exact: true,
    })
    .check();
  await sheet
    .getByRole("checkbox", { name: "I confirm conflicting node work has stopped.", exact: true })
    .check();
  await sheet.getByRole("button", { name: "Review change", exact: true }).click();
  const review = page.getByRole("alertdialog");
  await expect(review).toContainText(config.ownerId);
  await expect(review).not.toContainText("PRIVATE_BROWSER");
  await review.getByRole("button", { name: "Confirm", exact: true }).click();
  const recover = page.getByRole("button", { name: "Recover original receipt", exact: true });
  await expect(recover).toBeEnabled({ timeout: 20000 });
  const saved = await page.evaluate(() => Object.values(localStorage).join("\n"));
  expect(saved).toContain(requestId);
  expect(saved).not.toContain("PRIVATE_BROWSER");
  await recover.click();
  await expect(recover).toHaveCount(0, { timeout: 20000 });
  expect(requests).toBe(1);
  process.stdout.write(JSON.stringify({ requestId, admissions: requests }));
} finally {
  await browser.close();
}
