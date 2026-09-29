import { describe, expect, it } from "vitest";

import { DEFAULT_HOME_PREFS, parseHomePrefs } from "./home-prefs";

describe("parseHomePrefs", () => {
  it("reads unset, garbage and unknown layouts as the access default", () => {
    expect(parseHomePrefs(null)).toEqual({ layout: null, asked: false });
    expect(parseHomePrefs("{nope")).toEqual(DEFAULT_HOME_PREFS);
    expect(parseHomePrefs('{"layout":"finance"}')).toEqual(DEFAULT_HOME_PREFS);
  });

  it("keeps a layout the registry knows, which also means the question was answered", () => {
    expect(parseHomePrefs('{"layout":"operator"}')).toEqual({ layout: "operator", asked: true });
  });

  it("remembers a reset to the default as answered", () => {
    expect(parseHomePrefs('{"layout":null,"asked":true}')).toEqual({ layout: null, asked: true });
  });
});
