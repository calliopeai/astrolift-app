import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

const axe = readFileSync(path.resolve("node_modules/axe-core/axe.min.js"), "utf8");
const stories = [
  "screens-nvke-credential-collection--full",
  "screens-nvke-credential-collection--read-only",
  "screens-nvke-credential-collection--unknown-outcome",
  "screens-nvke-credential-collection--read-only-recovery",
  "screens-nvke-credential-collection--missing-authority",
  "screens-nvke-credential-collection--other-pending-request",
  "screens-nvke-operation--collect-credentials",
  "screens-nvke-metrics-authority--full",
  "screens-nvke-metrics-authority--empty",
  "screens-nvke-metrics-authority--read-only",
  "screens-nvke-metrics-authority--failure",
  "screens-nvke-credential-observations--retained-expiry",
  "screens-nvke-credential-observations--missing",
  "screens-nvke-credential-observations--failure",
  "screens-nvke-site--metrics",
  "screens-nvke-operation--credentials",
  "screens-tokens-scopepicker--nvke-automation",
  "screens-tokens-scopepicker--nvke-reader-role",
  "screens-nvke-native-maintenance-workflows--retained-rotation",
  "screens-nvke-native-maintenance-workflows--read-only",
  "screens-nvke-native-maintenance-workflows--missing-authority",
  "screens-nvke-native-maintenance-workflows--completed",
  "screens-nvke-native-maintenance-workflows--read-only-recovery",
  "screens-nvke-native-maintenance-workflows--retirement-not-inspected",
  "screens-nvke-operation--native-maintenance-controls",
  "screens-nvke-operation--native-workflow",
  "screens-nvke-operation--native-reset-unconfirmed",
  "screens-nvke-native-temporal--full",
  "screens-nvke-native-temporal--empty",
  "screens-nvke-native-temporal--read-only",
  "screens-nvke-native-temporal--failure",
  "screens-nvke-site--native-temporal",
  "screens-nvke-sites--full",
  "screens-nvke-site--full",
  "screens-nvke-native-actions--full",
  "screens-nvke-operation--uncertain",
  "screens-nvke-collections--full",
  "screens-nvke-inventory--observed",
  "screens-nvke-resource--full",
  "screens-nvke-resource-actions--editing",
  "screens-nvke-resource--with-editor",
  "screens-nvke-resource-actions--spire",
  "screens-nvke-resource-actions--reads",
  "screens-nvke-resource-actions--read-only-resource",
  "screens-nvke-diagnostics--spire",
  "screens-nvke-diagnostics--control-plane",
  "screens-nvke-diagnostics--history",
  "screens-nvke-operation--diagnostics",
  "screens-nvke-workload-access--full",
  "screens-nvke-workload-access--read-only",
  "screens-nvke-workload-access--empty",
  "screens-nvke-workload-access--failure",
  "screens-nvke-operation--imported-access",
  "screens-nvke-site--imported-access",
  "screens-nvke-maintenance--retained-failure",
  "screens-nvke-maintenance--accepted",
  "screens-nvke-maintenance--no-op",
  "screens-nvke-maintenance--missing",
  "screens-nvke-maintenance--retained-rotation",
  "screens-nvke-operation--maintenance",
];

test("credential collection reviews the exact authority and source before admission", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(
    "/iframe.html?id=screens-nvke-credential-collection--full&viewMode=story&globals=a11y.manual:!true"
  );
  await page.getByRole("button", { name: "Collect credentials", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Review change", exact: true })
    .click();
  const review = page.getByRole("alertdialog");
  await expect(review).toContainText("metrics.example.test/controller/metrics");
  await expect(review).toContainText("600 seconds");
  await expect(review).toContainText("123e4567-e89b-12d3-a456-426614174092");
  await expect(review).toHaveCSS("opacity", "1");
  await page.addScriptTag({ content: axe });
  const violations = await page.evaluate(async () => {
    const engine = (
      window as unknown as {
        axe: {
          run: (root: Element, options: unknown) => Promise<{ violations: { id: string }[] }>;
        };
      }
    ).axe;
    return (
      await engine.run(document.querySelector('[role="alertdialog"]')!, {
        runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
      })
    ).violations.map((item) => item.id);
  });
  expect(violations).toEqual([]);
  await page.screenshot({
    path: test.info().outputPath("nvke-credential-collection-review.png"),
    fullPage: true,
  });
  await review.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(review).not.toBeVisible();
});

test("credential recovery stays available without metrics authority or administrator access", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-nvke-credential-collection--read-only-recovery&viewMode=story"
  );
  await expect(
    page.getByRole("button", { name: "Recover original receipt", exact: true })
  ).toBeEnabled();
  await expect(page.getByRole("button", { name: "Collect credentials", exact: true })).toHaveCount(
    0
  );
  await expect(
    page.getByRole("button", { name: "Retry original collection", exact: true })
  ).toHaveCount(0);
});

test("metrics authority reviews the exact URL without retaining the bearer", async ({ page }) => {
  await page.goto("/iframe.html?id=screens-nvke-metrics-authority--full&viewMode=story");
  await page.getByRole("button", { name: "Configure connection", exact: true }).click();
  const sheet = page.getByRole("dialog");
  await sheet.getByRole("combobox").click();
  await page.getByRole("option", { name: "Replace credential bundle", exact: true }).click();
  await sheet.getByLabel("Metrics bearer token", { exact: true }).fill("PRIVATE_METRICS_TOKEN");
  await sheet.getByRole("button", { name: "Review change", exact: true }).click();
  const review = page.getByRole("alertdialog");
  await expect(review).toContainText("metrics authority version 2");
  await expect(review).toContainText("https://metrics.example.test/controller/metrics");
  await expect(review).not.toContainText("PRIVATE_METRICS_TOKEN");
  await review.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(sheet).not.toBeVisible();
  await page.getByRole("button", { name: "Configure connection", exact: true }).click();
  await page.getByRole("dialog").getByRole("combobox").click();
  await page.getByRole("option", { name: "Replace credential bundle", exact: true }).click();
  await expect(page.getByLabel("Metrics bearer token", { exact: true })).toHaveValue("");
});

test("credential views retain exact values and independent observation facts", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-nvke-credential-observations--retained-expiry&viewMode=story"
  );
  await expect(page.getByText("9007199254740993", { exact: true })).toBeVisible();
  await expect(page.getByText("Collection succeeded", { exact: true }).locator("..")).toContainText(
    "No"
  );
  await expect(
    page.getByText("Renewal automation enabled", { exact: true }).locator("..")
  ).toContainText("Not reported");
  await expect(page.getByText("2026-11-01T00:00:00Z", { exact: true })).toBeVisible();
  await expect(page.getByText(/Historical exporter reports/)).toContainText("not current health");
  await page.screenshot({
    path: test.info().outputPath("nvke-credential-observations.png"),
    fullPage: true,
  });
});

test("metrics long identifiers fit the viewport", async ({ page }) => {
  for (const id of [
    "screens-nvke-metrics-authority--long-strings",
    "screens-nvke-credential-observations--long-strings",
  ]) {
    await page.goto(`/iframe.html?id=${id}&viewMode=story`);
    await page.getByRole("heading").first().waitFor();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
    ).toBe(true);
  }
});

test("NVKE automation presets select only explicit NVKE scopes", async ({ page }) => {
  await page.goto("/iframe.html?id=screens-tokens-scopepicker--nvke-automation&viewMode=story");
  await page.getByRole("button", { name: "NVKE operator", exact: true }).click();
  await expect(page.getByRole("checkbox", { checked: true })).toHaveCount(3);
  await expect(page.getByRole("checkbox", { name: /^Administer NVKE/ })).toBeChecked();
  await expect(page.getByText("admin", { exact: true })).toHaveCount(0);
  await expect(
    page.getByText(/retrieve workload credentials and run native maintenance/)
  ).toBeVisible();
  await page.getByRole("button", { name: "NVKE reader", exact: true }).click();
  await expect(page.getByRole("checkbox", { checked: true })).toHaveCount(1);
  await expect(page.getByRole("checkbox", { name: /^Read NVKE/ })).toBeChecked();
  await page.screenshot({ path: test.info().outputPath("nvke-scope-picker.png"), fullPage: true });
});

test("NVKE operator preset cannot select scopes withheld by the current roles", async ({
  page,
}) => {
  await page.goto("/iframe.html?id=screens-tokens-scopepicker--nvke-reader-role&viewMode=story");
  await page.getByRole("button", { name: "NVKE operator", exact: true }).click();
  await expect(page.getByRole("checkbox", { checked: true })).toHaveCount(1);
  await expect(page.getByRole("checkbox", { name: /^Administer NVKE/ })).toBeDisabled();
  await expect(page.getByRole("checkbox", { name: /^Manage NVKE resources/ })).toBeDisabled();
});

test("nvke native Temporal review names exact authority and keeps credentials out of confirmation", async ({
  page,
}) => {
  await page.goto("/iframe.html?id=screens-nvke-native-temporal--full&viewMode=story");
  await page.getByRole("button", { name: "Configure connection", exact: true }).click();
  const sheet = page.getByRole("dialog");
  await sheet.getByRole("combobox").click();
  await page.getByRole("option", { name: "Replace credential bundle", exact: true }).click();
  await sheet.getByLabel("Native API key", { exact: true }).fill("PRIVATE_NATIVE_KEY");
  await sheet.getByRole("button", { name: "Review change", exact: true }).click();
  const review = page.getByRole("alertdialog");
  await expect(review).toContainText("site version 7");
  await expect(review).toContainText("native authority version 2");
  await expect(review).toContainText("native-controller-tasks");
  await expect(review).toContainText("Replace credential bundle");
  await expect(review).not.toContainText("PRIVATE_NATIVE_KEY");
  await review.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(review).not.toBeVisible();
  await expect(sheet).not.toBeVisible();
  await page.getByRole("button", { name: "Configure connection", exact: true }).click();
  await page.getByRole("dialog").getByRole("combobox").click();
  await page.getByRole("option", { name: "Replace credential bundle", exact: true }).click();
  await expect(page.getByLabel("Native API key", { exact: true })).toHaveValue("");
});

test("nvke native Temporal long identifiers stay within the viewport", async ({ page }) => {
  await page.goto("/iframe.html?id=screens-nvke-native-temporal--long-strings&viewMode=story");
  await page.getByRole("heading", { name: "Native Temporal", exact: true }).waitFor();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
});

test("nvke renewal failures expose retained ownership and distinguish schedule from expiry", async ({
  page,
}) => {
  await page.goto("/iframe.html?id=screens-nvke-maintenance--retained-failure&viewMode=story");
  await expect(page.getByText("Recorded state", { exact: true }).locator("..")).toContainText(
    "Failed"
  );
  await expect(
    page.getByText("Mutation lock retained after failure", { exact: true }).locator("..")
  ).toContainText("Yes");
  await expect(
    page.getByText("Scheduled check overdue at observation", { exact: true }).locator("..")
  ).toContainText("Yes");
  await expect(page.getByRole("note")).toContainText("2026-11-07T12:00:00Z");
  await expect(
    page.getByText(/Certificate expiry requires separate inspection data/)
  ).toBeVisible();
  await expect(page.getByRole("button")).toHaveCount(1);
});
test("nvke long maintenance workflow identifiers stay within a narrow viewport", async ({
  page,
}) => {
  await page.goto("/iframe.html?id=screens-nvke-maintenance--long-strings&viewMode=story");
  await page.getByRole("heading", { name: "Credential maintenance", exact: true }).waitFor();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
});

test("nvke imported enrollment names the exact cluster and reviewed source", async ({ page }) => {
  await page.goto("/iframe.html?id=screens-nvke-workload-access--full&viewMode=story");
  await page.getByRole("button", { name: "Enroll workload access", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("123e4567-e89b-12d3-a456-426614174088");
  await expect(dialog).toContainText("NICo research fleet, site version 7");
  await expect(dialog).toContainText("observation version 9");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).not.toBeVisible();
});
test("nvke imported revocation explains independent ownership sources", async ({ page }) => {
  await page.goto("/iframe.html?id=screens-nvke-workload-access--full&viewMode=story");
  await page
    .getByRole("row")
    .filter({ has: page.locator("code", { hasText: "123e4567-e89b-12d3-a456-426614174088" }) })
    .getByRole("button")
    .click();
  await page.getByRole("menuitem", { name: "Revoke imported grant" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("Independent native CRD or Astro create-receipt");
  await expect(dialog).toContainText("binding version 1");
  await dialog.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(dialog).not.toBeVisible();
});
for (const mode of ["light", "dark"]) {
  for (const id of stories) {
    test(`nvke accessibility ${id} in ${mode}`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion: "reduce" });
      // This test owns the Axe scan; the addon's automatic scan would race it.
      await page.goto(
        `/iframe.html?id=${id}&viewMode=story&globals=mode:${mode};a11y.manual:!true`
      );
      if (id.startsWith("screens-tokens-scopepicker--"))
        await page.getByRole("group", { name: "NVKE", exact: true }).waitFor();
      else await page.locator("#storybook-root").locator("h1,h2").first().waitFor();
      if (mode === "dark") await expect(page.locator("html")).toHaveClass(/dark/);
      else await expect(page.locator("html")).not.toHaveClass(/dark/);
      await page.addScriptTag({ content: axe });
      const violations = await page.evaluate(async () => {
        const engine = (
          window as unknown as {
            axe: {
              run: (
                root: Element,
                options: unknown
              ) => Promise<{ violations: { id: string; nodes: { html: string }[] }[] }>;
            };
          }
        ).axe;
        const result = await engine.run(document.querySelector("#storybook-root")!, {
          runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
        });
        return result.violations.map((item) => ({
          rule: item.id,
          nodes: item.nodes.map((node) => node.html),
        }));
      });
      expect(violations).toEqual([]);
    });
  }
}

test("nvke native mutation requires a target-scoped confirmation", async ({ page }) => {
  await page.goto("/iframe.html?id=screens-nvke-native-actions--full&viewMode=story");
  await page.getByRole("combobox").click();
  await page.getByRole("option", { name: "controller · createCluster", exact: true }).click();
  await page.getByLabel("Native parameters (JSON)").fill('{"body":{"name":"reviewed-cluster"}}');
  await page.getByRole("button", { name: "Review change", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("createCluster");
  await expect(dialog).toContainText("reviewed-cluster");
  await expect(dialog).toContainText("NICo research fleet");
  await expect(dialog).toContainText("site version 7");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByLabel("Native parameters (JSON)")).toHaveValue(
    '{"body":{"name":"reviewed-cluster"}}'
  );
});

test("nvke credentials are write-only and discarded when a configuration sheet closes", async ({
  page,
}) => {
  await page.goto("/iframe.html?id=screens-nvke-site--full&viewMode=story");
  const row = page
    .getByRole("row")
    .filter({ has: page.locator("code", { hasText: /^controller$/ }) });
  await row.getByRole("button").click();
  await page.getByRole("menuitem", { name: /Configure connection/ }).click();
  const sheet = page.getByRole("dialog");
  await sheet.getByRole("combobox").click();
  await page.getByRole("option", { name: "Replace credential bundle" }).click();
  await sheet.getByLabel("Bearer token").fill("PRIVATE_STORY_CREDENTIAL");
  await sheet.getByLabel("Client private key (PEM)").fill("PRIVATE_STORY_KEY");
  await page.keyboard.press("Escape");
  await expect(sheet).not.toBeVisible();
  await row.getByRole("button").click();
  await page.getByRole("menuitem", { name: /Configure connection/ }).click();
  await sheet.getByRole("combobox").click();
  await page.getByRole("option", { name: "Replace credential bundle" }).click();
  await expect(sheet.getByLabel("Bearer token")).toHaveValue("");
  await expect(sheet.getByLabel("Client private key (PEM)")).toHaveValue("");
});

for (const mode of ["light", "dark"]) {
  test(`nvke removal confirmation is accessible in ${mode}`, async ({ page }) => {
    await page.goto(
      `/iframe.html?id=screens-nvke-site--full&viewMode=story&globals=mode:${mode};a11y.manual:!true`
    );
    await page.getByRole("button", { name: "Remove registration", exact: true }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("Existing upstream infrastructure continues running");
    await page.addScriptTag({ content: axe });
    const violations = await page.evaluate(async () => {
      const engine = (
        window as unknown as {
          axe: {
            run: (
              root: Element,
              options: unknown
            ) => Promise<{ violations: { id: string; nodes?: unknown[] }[] }>;
          };
        }
      ).axe;
      const result = await engine.run(document.querySelector('[role="alertdialog"]')!, {
        runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
      });
      return result.violations.map((v) => ({ id: v.id, nodes: v.nodes }));
    });
    expect(violations).toEqual([]);
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(dialog).not.toBeVisible();
  });
}

test("nvke resource edit confirms its fixed target and retains opaque integer text", async ({
  page,
}) => {
  await page.goto("/iframe.html?id=screens-nvke-resource-actions--editing&viewMode=story");
  const text = '{"body":{"desiredCount":9007199254740993}}';
  await expect(page.getByLabel("Native parameters (JSON)")).toHaveValue(text);
  await page.getByRole("button", { name: "Review change", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("research-cluster");
  await expect(dialog).toContainText("updateCluster");
  await expect(dialog).toContainText("site version 7");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByLabel("Native parameters (JSON)")).toHaveValue(text);
});

test("nvke collection creation confirms its resource target without converting native integers", async ({
  page,
}) => {
  await page.goto("/iframe.html?id=screens-nvke-inventory--observed&viewMode=story");
  await page.locator("#nvke-action").click();
  await page.getByRole("option", { name: "controller · createCluster", exact: true }).click();
  const raw = '{"body":{"name":"new-collection-cluster","count":9007199254740993}}';
  await page.getByLabel("Native parameters (JSON)").fill(raw);
  await page.getByRole("button", { name: "Review change", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("new-collection-cluster");
  await expect(dialog).toContainText("createCluster");
  await expect(dialog).toContainText("site version 7");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByLabel("Native parameters (JSON)")).toHaveValue(raw);
});

test("nvke SPIRE editing confirms both cluster and requested server", async ({ page }) => {
  await page.goto("/iframe.html?id=screens-nvke-resource-actions--spire&viewMode=story");
  await page.getByLabel("SPIRE server address", { exact: true }).fill("spire.new.test");
  await page.getByLabel("SPIRE server port", { exact: true }).fill("30441");
  await page.getByRole("button", { name: "Review change", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("research-cluster");
  await expect(dialog).toContainText("updateClusterSpireServer");
  await expect(dialog).toContainText("spire.new.test");
  await expect(dialog).toContainText("30441");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByLabel("SPIRE server address", { exact: true })).toHaveValue(
    "spire.new.test"
  );
});

test("nvke diagnostics show exact counters and unreported readiness as historical metadata", async ({
  page,
}) => {
  await page.goto("/iframe.html?id=screens-nvke-diagnostics--control-plane&viewMode=story");
  await expect(page.getByText("9007199254740993")).toHaveCount(2);
  await expect(page.getByText("Reported Ready nodes", { exact: true }).locator("..")).toContainText(
    "Not reported"
  );
  await expect(page.getByRole("note")).toContainText("saved observation");
});

test("nvke read-only diagnostic tables scroll with the keyboard", async ({ page }) => {
  await page.goto("/iframe.html?id=screens-nvke-diagnostics--spire&viewMode=story");
  const region = page.getByRole("region", { name: "Diagnostics", exact: true });
  await region.focus();
  await expect(region).toBeFocused();
  await page.keyboard.press("ArrowRight");
  await expect.poll(() => region.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
});

test("nvke workflow reset reviews exact string IDs and clears private evidence", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-nvke-native-maintenance-workflows--retained-rotation&viewMode=story"
  );
  const select = page.getByRole("button", { name: "Select for reset", exact: true });
  await expect(select.first()).toBeDisabled();
  await select.nth(1).click();
  const sheet = page.getByRole("dialog");
  await sheet.getByLabel("Reset rationale (private)", { exact: true }).fill("PRIVATE_RESET_REASON");
  await sheet
    .getByLabel("Conflicting node-work evidence reference (private)", { exact: true })
    .fill("PRIVATE_NODE_EVIDENCE");
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
  await expect(review).toContainText("9007199254740993");
  await expect(review).toContainText("9007199254740992");
  await expect(review).toContainText("rotation-owner-更新");
  await expect(review).toContainText("restart_rewrite");
  await expect(review).not.toContainText("PRIVATE");
  await page.screenshot({
    path: test.info().outputPath("native-reset-review.png"),
    fullPage: true,
  });
  await review.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(review).not.toBeVisible();
  await expect(sheet).not.toBeVisible();
  await select.nth(1).click();
  await expect(page.getByLabel("Reset rationale (private)", { exact: true })).toHaveValue("");
  await expect(
    page.getByLabel("Conflicting node-work evidence reference (private)", { exact: true })
  ).toHaveValue("");
});

test("nvke workflow retirement requires acknowledgement of the exact node and owner", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-nvke-native-maintenance-workflows--retained-rotation&viewMode=story&globals=a11y.manual:!true"
  );
  await page.getByRole("button", { name: "Submit native workflow", exact: true }).click();
  const sheet = page.getByRole("dialog");
  await sheet.getByRole("combobox").click();
  await page.getByRole("option", { name: "RetireLostEtcdRotationNode", exact: true }).click();
  const node = "123e4567-e89b-12d3-a456-426614174099";
  await sheet.getByLabel("Native parameters (JSON)", { exact: true }).fill(
    JSON.stringify({
      clusterID: "123e4567-e89b-12d3-a456-426614174088",
      nodeID: node,
      rotationWorkflowID: "rotation-owner-更新",
    })
  );
  await sheet.getByRole("button", { name: "Review change", exact: true }).click();
  await expect(page.getByRole("alertdialog")).not.toBeVisible();
  await sheet
    .getByRole("checkbox", {
      name: "I acknowledge permanent removal of the exact control-plane node in this input.",
      exact: true,
    })
    .check();
  await sheet.getByRole("button", { name: "Review change", exact: true }).click();
  const review = page.getByRole("alertdialog");
  await expect(review).toContainText(node);
  await expect(review).toContainText("rotation-owner-更新");
  await page.addScriptTag({ content: axe });
  const violations = await page.evaluate(async () => {
    const engine = (
      window as unknown as {
        axe: {
          run: (
            root: Element,
            options: unknown
          ) => Promise<{ violations: { id: string; nodes?: unknown[] }[] }>;
        };
      }
    ).axe;
    return (
      await engine.run(document.querySelector('[role="alertdialog"]')!, {
        runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
      })
    ).violations.map((item) => ({ id: item.id, nodes: item.nodes }));
  });
  expect(violations).toEqual([]);
});

test("nvke native workflow long identifiers fit the viewport", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-nvke-native-maintenance-workflows--long-strings&viewMode=story"
  );
  await page.getByRole("heading", { name: "Native maintenance workflows", exact: true }).waitFor();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
});

test("nvke read-only callers can recover receipts without enabling native admission", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-nvke-native-maintenance-workflows--read-only-recovery&viewMode=story"
  );
  await expect(
    page.getByRole("button", { name: "Recover original receipt", exact: true })
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Submit native workflow", exact: true })
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Inspect RotateEtcdEncryptionKey", exact: true })
  ).toBeDisabled();
});

test("nvke retirement stays unavailable before inspecting the retained rotation", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-nvke-native-maintenance-workflows--retirement-not-inspected&viewMode=story"
  );
  await page.getByRole("button", { name: "Submit native workflow", exact: true }).click();
  await page.getByRole("dialog").getByRole("combobox").click();
  await expect(
    page.getByRole("option", { name: "RetireLostEtcdRotationNode", exact: true })
  ).toBeDisabled();
});
