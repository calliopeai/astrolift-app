import { beforeEach, describe, expect, it } from "vitest";

import {
  DEFAULT_APPEARANCE,
  MIN_ACCENT_CONTRAST,
  STORAGE_KEY,
  accentContrast,
  applyAppearance,
  clearAppearance,
  contrastRatio,
  inkFor,
  parseCustomAccent,
  normalize,
  normalizePartial,
  readAppearance,
  resolveAppearance,
  writeAppearance,
} from "./appearance";

/**
 * The precedence rule is the whole point of the org policy (#135):
 * locked org theme > personal > org default > shipped default.
 *
 * The regression these guard against: `readAppearance` used to return a
 * fully-populated preference when nothing was stored, so "never chose" was
 * indistinguishable from "chose the defaults". Because personal is spread
 * last, that full object overrode the org default for every user who had
 * never opened the picker — which is every user. The org default was inert.
 */
describe("resolveAppearance precedence", () => {
  const orgTheme = { ground: "charcoal", accent: "copper" } as const;

  it("falls back to the shipped default with no policy and no preference", () => {
    const { value, locked } = resolveAppearance(null, null);
    expect(value).toEqual(DEFAULT_APPEARANCE);
    expect(locked).toBe(false);
  });

  it("applies the org default when the person has never chosen", () => {
    const { value } = resolveAppearance(null, { orgDefault: orgTheme });
    expect(value.ground).toBe("charcoal");
    expect(value.accent).toBe("copper");
    // axes the org did not pin still come from the shipped default
    expect(value.density).toBe(DEFAULT_APPEARANCE.density);
    expect(value.corners).toBe(DEFAULT_APPEARANCE.corners);
  });

  it("lets a personal choice beat the org default", () => {
    const { value, locked } = resolveAppearance({ accent: "ice" }, { orgDefault: orgTheme });
    expect(value.accent).toBe("ice");
    // and the org still decides the axes the person left alone
    expect(value.ground).toBe("charcoal");
    expect(locked).toBe(false);
  });

  it("lets a locked org theme beat a personal choice", () => {
    const { value, locked } = resolveAppearance(
      { accent: "ice", ground: "paper" },
      { orgDefault: orgTheme, locked: true }
    );
    expect(value.accent).toBe("copper");
    expect(value.ground).toBe("charcoal");
    expect(locked).toBe(true);
  });

  it("locks to the shipped default when locked with no org theme", () => {
    const { value, locked } = resolveAppearance({ accent: "ice" }, { locked: true });
    expect(value).toEqual(DEFAULT_APPEARANCE);
    expect(locked).toBe(true);
  });
});

describe("normalizePartial", () => {
  it("keeps a partial partial", () => {
    expect(normalizePartial({ accent: "copper" })).toEqual({ accent: "copper" });
  });

  it("drops unknown keys and values instead of defaulting them", () => {
    // An axis renamed since the policy was written must degrade to "not set",
    // not to the shipped default — otherwise a stale org policy silently
    // pins axes nobody chose.
    expect(normalizePartial({ accent: "purple", ground: "black", nope: 1 })).toEqual({
      ground: "black",
    });
  });

  it("returns an empty object for junk", () => {
    expect(normalizePartial("black")).toEqual({});
    expect(normalizePartial(null)).toEqual({});
  });

  it("differs from normalize, which always fills every axis", () => {
    expect(Object.keys(normalizePartial({ accent: "copper" }))).toEqual(["accent"]);
    expect(Object.keys(normalize({ accent: "copper" })).sort()).toEqual([
      "accent",
      "corners",
      "density",
      "ground",
    ]);
  });
});

/**
 * The actual regression site. `resolveAppearance` above was always correct;
 * the bug was that `readAppearance` returned a filled-in preference when
 * storage was empty, so the org default lost to a preference nobody had set.
 * These fail against that old behaviour, which is the point of having them.
 */
describe("readAppearance", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("returns null when nothing has ever been stored", () => {
    expect(readAppearance()).toBeNull();
  });

  it("returns null for a stored value with no recognisable axes", () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ accent: "purple" }));
    expect(readAppearance()).toBeNull();
  });

  it("returns null rather than throwing on malformed JSON", () => {
    window.localStorage.setItem(STORAGE_KEY, "{not json");
    expect(readAppearance()).toBeNull();
  });

  it("returns only the axes that were stored", () => {
    writeAppearance({ accent: "copper" });
    expect(readAppearance()).toEqual({ accent: "copper" });
  });

  it("an unset preference lets the org default through end to end", () => {
    // the whole bug, expressed once
    const policy = { orgDefault: { accent: "copper" } as const };
    expect(resolveAppearance(readAppearance(), policy).value.accent).toBe("copper");
    writeAppearance({ accent: "ice" });
    expect(resolveAppearance(readAppearance(), policy).value.accent).toBe("ice");
    clearAppearance();
    expect(resolveAppearance(readAppearance(), policy).value.accent).toBe("copper");
  });
});

/**
 * Palette option A: a custom accent is any colour, stored in `accent` like a
 * preset, and it only ever lands where it can be seen against the ground.
 */
describe("custom accent", () => {
  it("parses the forms people type into one stored shape", () => {
    expect(parseCustomAccent("#2F9E52")).toBe("#2f9e52");
    expect(parseCustomAccent("2f9e52")).toBe("#2f9e52");
    expect(parseCustomAccent("#abc")).toBe("#aabbcc");
    expect(parseCustomAccent("green")).toBeNull();
    expect(parseCustomAccent("#12345")).toBeNull();
  });

  it("measures WCAG contrast", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrastRatio("#777777", "#777777")).toBeCloseTo(1, 5);
  });

  it("holds a custom accent to 3:1 against the ground", () => {
    expect(accentContrast("#2f9e52", "black").ok).toBe(true);
    expect(accentContrast("#101010", "black").ok).toBe(false);
    expect(accentContrast("#f0e68c", "paper").ratio).toBeLessThan(MIN_ACCENT_CONTRAST);
  });

  it("keeps a custom accent through storage like a preset", () => {
    writeAppearance({ accent: "#d94f8a" });
    expect(readAppearance()).toEqual({ accent: "#d94f8a" });
    expect(normalizePartial({ accent: "#D94F8A" })).toEqual({});
  });

  it("falls back to the default accent on a ground it cannot be read on", () => {
    expect(normalize({ ground: "black", accent: "#e8e0ff" }).accent).toBe("#e8e0ff");
    expect(normalize({ ground: "paper", accent: "#e8e0ff" }).accent).toBe(
      DEFAULT_APPEARANCE.accent
    );
  });

  it("picks the ink with more contrast for text on the accent", () => {
    expect(inkFor("#f0e68c")).toBe("#000000");
    expect(inkFor("#1a237e")).toBe("#ffffff");
  });

  it("paints only the action tokens inline, and clears them for a preset", () => {
    const el = document.createElement("html");
    applyAppearance({ ...DEFAULT_APPEARANCE, accent: "#d94f8a" }, el);
    expect(el.dataset.accent).toBe("custom");
    expect(el.style.getPropertyValue("--brand-primary")).toBe("#d94f8a");
    expect(el.style.getPropertyValue("--success")).toBe("");
    applyAppearance({ ...DEFAULT_APPEARANCE, accent: "ice" }, el);
    expect(el.dataset.accent).toBe("ice");
    expect(el.style.getPropertyValue("--brand-primary")).toBe("");
  });
});
