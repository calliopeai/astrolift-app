import { describe, expect, it } from "vitest";

import { DEFAULT_VIZ_PREFS, parseVizPrefs, resolveMotion } from "./viz-prefs";

describe("parseVizPrefs", () => {
  it("falls back to defaults for missing or broken storage", () => {
    expect(parseVizPrefs(null)).toEqual(DEFAULT_VIZ_PREFS);
    expect(parseVizPrefs("{nope")).toEqual(DEFAULT_VIZ_PREFS);
    expect(parseVizPrefs("42")).toEqual(DEFAULT_VIZ_PREFS);
  });

  it("keeps valid fields and drops unknown ones field by field", () => {
    const parsed = parseVizPrefs(
      JSON.stringify({ fleetView: "hive", workflowView: "gone", flowParticles: false })
    );
    expect(parsed).toEqual({ ...DEFAULT_VIZ_PREFS, fleetView: "hive", flowParticles: false });
  });
});

describe("resolveMotion", () => {
  it("follows the OS only when set to system", () => {
    expect(resolveMotion("system", true)).toBe("reduced");
    expect(resolveMotion("system", false)).toBe("full");
    expect(resolveMotion("full", true)).toBe("full");
    expect(resolveMotion("reduced", false)).toBe("reduced");
  });
});
