import { chromium, expect } from "@playwright/test";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const story = new URL(config.storyOrigin),
  endpoint = new URL(config.endpoint);
if (
  [story, endpoint].some(
    (url) =>
      url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.username || url.password
  )
)
  throw new Error("Qualification requires owned loopback services");
let stage = "launch";
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.addInitScript(
    (value) => {
      window.__NVKE_LOCAL_AGENT__ = value;
    },
    {
      endpoint: config.endpoint,
      token: config.token,
      organizationId: config.organizationId,
      actorId: config.actorId,
      siteId: config.siteId,
    }
  );
  const writes = { configure: 0, verify: 0, disable: 0, enable: 0, remove: 0 };
  await page.route(config.endpoint, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const body = route.request().postDataJSON();
    if (body.operationName === "ConfigureNvkeOperatorAuthority") {
      writes.configure++;
      if (config.redirectUrl) {
        await route.fulfill({
          status: 307,
          headers: { location: config.redirectUrl, "access-control-allow-origin": "*" },
          body: "",
        });
        return;
      }
      const response = await route.fetch();
      expect((await response.json()).data?.configureNvkeOperatorAuthority?.ok).toBe(true);
      await route.abort("failed");
      return;
    }
    if (body.operationName === "VerifyNvkeOperatorAuthority") writes.verify++;
    if (body.operationName === "SetNvkeOperatorAuthorityEnabled")
      writes[body.variables.input.enabled ? "enable" : "disable"]++;
    if (body.operationName === "RemoveNvkeOperatorAuthority") writes.remove++;
    return route.continue();
  });
  stage = "open-panel";
  await page.goto(
    `${story.origin}/iframe.html?id=screens-nvke-local-agent-qualification--connected&viewMode=story`
  );
  const configure = page.getByRole("button", { name: "Configure connection", exact: true });
  await expect(configure).toBeEnabled({ timeout: 20000 });
  stage = "configure-form";
  await configure.click();
  const sheet = page.getByRole("dialog");
  await sheet.getByLabel("Endpoint", { exact: true }).fill(config.agentEndpoint);
  await sheet.getByLabel("Agent instance GUID", { exact: true }).fill(config.instanceId);
  await sheet.getByLabel("Bundle manifest SHA-256", { exact: true }).fill(config.bundleSha256);
  await sheet.getByLabel("Certificate authority (PEM)", { exact: true }).fill(config.caPem);
  await sheet
    .getByLabel("Client certificate (PEM)", { exact: true })
    .fill(config.clientCertificatePem);
  await sheet.getByLabel("Client private key (PEM)", { exact: true }).fill(config.clientKeyPem);
  await sheet.getByRole("button", { name: "Review change", exact: true }).click();
  const confirm = page.getByRole("alertdialog");
  await expect(confirm).toContainText(config.instanceId);
  await expect(page.locator("#nvke-agent-clientKeyPem")).toHaveValue("");
  stage = "lost-configuration-reply";
  await confirm.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "NVKE configuration was refused or could not be confirmed.",
    { timeout: 20000 }
  );
  await expect(configure).toBeDisabled();
  if (config.redirectUrl) {
    process.stdout.write(JSON.stringify({ writes, redirectProbe: true }));
  } else {
    stage = "refresh-current-versions";
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    const verify = page.getByRole("button", { name: "Verify agent", exact: true });
    await expect(verify).toBeEnabled({ timeout: 20000 });
    async function action(label) {
      stage = label;
      await page.getByRole("button", { name: label, exact: true }).click();
      await page
        .getByRole("alertdialog")
        .getByRole("button", { name: "Confirm", exact: true })
        .click();
    }
    await action("Verify agent");
    await expect(page.getByText(/Agent identity verified ·/)).toBeVisible({ timeout: 20000 });
    await action("Disable agent");
    await expect(page.getByRole("button", { name: "Enable agent", exact: true })).toBeEnabled({
      timeout: 20000,
    });
    await expect(verify).toBeDisabled();
    await action("Enable agent");
    await expect(verify).toBeEnabled({ timeout: 20000 });
    await action("Verify agent");
    await expect(page.getByText(/Agent identity verified ·/)).toBeVisible({ timeout: 20000 });
    await action("Remove agent connection");
    await expect(page.getByRole("button", { name: "Verify agent", exact: true })).toHaveCount(0, {
      timeout: 20000,
    });
    await expect(configure).toBeEnabled({ timeout: 20000 });
    const retained = await page.evaluate(() =>
      [...Object.values(localStorage), ...Object.values(sessionStorage)].join("\n")
    );
    expect(retained).not.toContain(config.token);
    expect(retained).not.toContain("PRIVATE KEY");
    process.stdout.write(
      JSON.stringify({
        writes,
        recoveredConfiguration: true,
        privateDraftCleared: true,
        removed: true,
      })
    );
  }
} catch (error) {
  process.stderr.write(
    `Owned agent browser qualification failed at ${stage} (${error?.name ?? "Error"})\n`
  );
  process.exitCode = 1;
} finally {
  await browser.close();
}
