import { readFileSync } from "node:fs";
import path from "node:path";
import { test, expect } from "@playwright/test";
const axe = readFileSync(path.resolve("node_modules/axe-core/axe.min.js"), "utf8");
const states = [
  "full",
  "read-only",
  "missing-connection",
  "loading",
  "failure",
  "unknown-outcome",
  "read-only-recovery",
  "other-pending-request",
  "long-strings",
  "width-768",
];
for (const mode of ["light", "dark"])
  for (const state of states)
    test(`provider collection ${state} in ${mode}`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.goto(
        `/iframe.html?id=screens-nvke-provider-collection--${state}&viewMode=story&globals=mode:${mode};a11y.manual:!true`
      );
      await page
        .getByRole("heading", { name: "Collect provider observations", exact: true })
        .waitFor();
      if (mode === "dark") await expect(page.locator("html")).toHaveClass(/dark/);
      if (state === "read-only-recovery") {
        await expect(
          page.getByRole("button", { name: "Collect providers", exact: true })
        ).toHaveCount(0);
        await expect(
          page.getByRole("button", { name: "Recover original receipt", exact: true })
        ).toBeEnabled();
      }
      if (state === "full" || state === "long-strings") {
        await page.getByRole("button", { name: "Collect providers", exact: true }).click();
        await page.getByRole("button", { name: "Review change", exact: true }).click();
        const dialog = page.getByRole("alertdialog");
        await expect(dialog).toContainText("123e4567-e89b-12d3-a456-426614174091");
        await expect(dialog).toContainText("version 3");
        await expect(dialog).toContainText("600 seconds");
        await expect(dialog).toContainText("92c39b8ad563ebe5ef705f781b3038b901aba10f");
        if (state === "full")
          await page.screenshot({
            path: `/tmp/astrolift-nvke-provider-collection-${mode}.png`,
            fullPage: true,
          });
      }
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
      ).toBe(true);
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
          await engine.run(document.body, {
            runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
          })
        ).violations.map((row) => ({ id: row.id, nodes: row.nodes.map((node) => node.html) }));
      });
      expect(violations).toEqual([]);
    });
