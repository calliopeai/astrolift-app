import { describe, expect, it } from "vitest";

import { DEFAULT_DISPLAY_PREFS, parseDisplayPrefs } from "./display-prefs";

describe("parseDisplayPrefs", () => {
  it("defaults to showing restricted settings read-only", () => {
    expect(parseDisplayPrefs(null)).toEqual({ restrictedSettings: "show" });
    expect(parseDisplayPrefs("{nope")).toEqual(DEFAULT_DISPLAY_PREFS);
    expect(parseDisplayPrefs('{"restrictedSettings":"sideways"}')).toEqual(DEFAULT_DISPLAY_PREFS);
  });

  it("keeps a valid choice", () => {
    expect(parseDisplayPrefs('{"restrictedSettings":"hide"}')).toEqual({
      restrictedSettings: "hide",
    });
  });
});
