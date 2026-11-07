import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

const axe = readFileSync(path.resolve("node_modules/axe-core/axe.min.js"), "utf8");
const stories = [
  "screens-nvke-sites--full",
  "screens-nvke-site--full",
  "screens-nvke-native-actions--full",
  "screens-nvke-operation--uncertain",
];
for (const mode of ["light", "dark"]) {
  for (const id of stories) {
    test(`nvke accessibility ${id} in ${mode}`, async ({ page }) => {
      await page.goto(`/iframe.html?id=${id}&viewMode=story&globals=mode:${mode}`);
      await page.locator("#storybook-root").locator("h1,h2").first().waitFor();
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
    await page.goto(`/iframe.html?id=screens-nvke-site--full&viewMode=story&globals=mode:${mode}`);
    await page.getByRole("button", { name: "Remove registration", exact: true }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("Existing upstream infrastructure continues running");
    await page.addScriptTag({ content: axe });
    const violations = await page.evaluate(async () => {
      const engine = (
        window as unknown as {
          axe: {
            run: (root: Element, options: unknown) => Promise<{ violations: { id: string }[] }>;
          };
        }
      ).axe;
      const result = await engine.run(document.querySelector('[role="alertdialog"]')!, {
        runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
      });
      return result.violations.map((v) => v.id);
    });
    expect(violations).toEqual([]);
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(dialog).not.toBeVisible();
  });
}
