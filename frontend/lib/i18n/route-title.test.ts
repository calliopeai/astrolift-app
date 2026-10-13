import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { type AbstractIntlMessages, createTranslator } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import de from "@/messages/de.json";

import { routeTitle } from "./route-title";

// The server translator, as `getTranslations` builds it for a German viewer.
vi.mock("next-intl/server", () => ({
  getTranslations: async (namespace: string) =>
    createTranslator({ locale: "de", messages: de as AbstractIntlMessages, namespace }),
}));

describe("routeTitle (#2381)", () => {
  it("names the page, where it sits and the product in the viewer's language", async () => {
    const titles = de.routeTitles;
    expect(await routeTitle("security", "settings")()).toEqual({
      title: { absolute: `${titles.security} · ${titles.settings} · Astrolift` },
    });
    expect(titles.security).not.toBe("Security");
  });

  it("is the only way a route titles itself: no hard-coded metadata titles", () => {
    const app = path.resolve(__dirname, "../../app");
    const routes = (dir: string): string[] =>
      readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) return routes(full);
        return /^(page|layout)\.tsx$/.test(entry.name) ? [full] : [];
      });
    const hardCoded = routes(app)
      .filter((file) => /export const metadata\b/.test(readFileSync(file, "utf8")))
      .map((file) => path.relative(app, file));
    // The root layout keeps the product name as the default and the template.
    expect(hardCoded).toEqual(["layout.tsx"]);
  });
});
