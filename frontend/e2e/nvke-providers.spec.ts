import { readFileSync } from "node:fs";
import path from "node:path";
import { test, expect } from "@playwright/test";
const axe = readFileSync(path.resolve("node_modules/axe-core/axe.min.js"), "utf8");
const states = [
  "recent-check-manager-down",
  "partial",
  "empty",
  "loading",
  "failure",
  "identity-failure",
  "not-installed",
  "long-strings",
  "width-768",
];
for (const mode of ["light", "dark"])
  for (const state of states) {
    test(`provider observation ${state} in ${mode}`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.goto(
        `/iframe.html?id=screens-nvke-infrastructure-providers--${state}&viewMode=story&globals=mode:${mode};a11y.manual:!true`
      );
      await page.getByRole("heading", { name: "Infrastructure providers", exact: true }).waitFor();
      if (mode === "dark") await expect(page.locator("html")).toHaveClass(/dark/);
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
      ).toBe(true);
      if (state === "recent-check-manager-down") {
        await expect(page.getByText("Check succeeded", { exact: true })).toBeVisible();
        await expect(page.getByText("Degraded", { exact: true })).toBeVisible();
        await expect(page.getByText("9007199254740993", { exact: true })).toHaveCount(2);
        await page.screenshot({
          path: `/tmp/astrolift-nvke-provider-history-${mode}.png`,
          fullPage: true,
        });
      }
      if (state === "failure" || state === "loading") {
        await expect(page.getByRole("combobox")).toHaveCount(0);
        await expect(page.getByText("9007199254740993", { exact: true })).toHaveCount(0);
      }
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
        return (
          await engine.run(document.querySelector("#storybook-root")!, {
            runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
          })
        ).violations.map((row) => ({ id: row.id, nodes: row.nodes.map((node) => node.html) }));
      });
      expect(violations).toEqual([]);
    });
  }

for (const mode of ["light", "dark"])
  test(`provider operation history in ${mode}`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(
      `/iframe.html?id=screens-nvke-operation--providers&viewMode=story&globals=mode:${mode};a11y.manual:!true`
    );
    await expect(
      page.getByRole("heading", { name: "infrastructure.providers", exact: true })
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Infrastructure providers", exact: true })
    ).toBeVisible();
    await expect(
      page.getByText("The provider observation was saved.", { exact: false })
    ).toBeVisible();
    await expect(page.getByText("Check succeeded", { exact: true })).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
    ).toBe(true);
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
        await engine.run(document.querySelector("#storybook-root")!, {
          runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
        })
      ).violations.map((row) => row.id);
    });
    expect(violations).toEqual([]);
  });
