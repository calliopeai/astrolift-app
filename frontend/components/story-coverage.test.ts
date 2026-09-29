import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Storybook first (frontend/bootstrap.md): no component reaches the app
 * unless it is in Storybook. Every file under components/ that exports a
 * component has a sibling `.stories.tsx`; `stories.test.tsx` renders them.
 *
 * Enforced hard, with no allowlist (Leo, 2026-09-28): the list below is the
 * work left, and it only reaches empty by writing the stories.
 */
const ROOT = path.resolve(__dirname);

function componentFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return componentFiles(full);
    if (!name.endsWith(".tsx") || /\.(stories|test)\.tsx$/.test(name)) return [];
    const src = readFileSync(full, "utf8");
    const exportsComponent =
      /export (default )?(function|const) [A-Z]/.test(src) || /export \{[^}]*\b[A-Z]/.test(src);
    return exportsComponent ? [full] : [];
  });
}

describe("story coverage", () => {
  it("every component has a story", () => {
    const missing = componentFiles(ROOT)
      .filter((file) => {
        try {
          statSync(file.replace(/\.tsx$/, ".stories.tsx"));
          return false;
        } catch {
          return true;
        }
      })
      .map((file) => path.relative(ROOT, file))
      .sort();

    expect(missing, `components with no story (${missing.length})`).toEqual([]);
  });
});
