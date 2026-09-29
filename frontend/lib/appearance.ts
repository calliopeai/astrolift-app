/**
 * Appearance preferences — the axes a user can set for themselves.
 *
 * Light/dark stays with next-themes (`class` on <html>). Everything here is
 * orthogonal to it and rides on data attributes that `app/globals.css`
 * resolves into the existing design tokens, so no component needs to know a
 * theme system exists:
 *
 *   data-ground   surface ramp      black | charcoal | emerald | paper | mist
 *   data-accent   action + charts   green | copper | ice | periwinkle | amber
 *                                   | custom (a `#rrggbb` the person picked)
 *   data-density  layout scale      compact | cards
 *   --radius      corner radius     inline style
 *
 * A per-person display preference, not org state. When signed in the
 * person's server preferences hold it (#2154, lib/ui-prefs-sync.ts); the
 * browser copy stays because it must apply before first paint (see the inline
 * script in layout.tsx), which rules out waiting for a fetch. Reads are
 * defensive: Safari private mode and embedded webviews throw on localStorage
 * access rather than returning null.
 */

export const GROUNDS = {
  black: { label: "Black", mode: "dark" },
  charcoal: { label: "Charcoal", mode: "dark" },
  emerald: { label: "Emerald", mode: "dark" },
  paper: { label: "Paper", mode: "light" },
  mist: { label: "Mist", mode: "light" },
} as const;

export const ACCENTS = {
  green: { label: "Green", swatch: "#2f9e52" },
  copper: { label: "Copper", swatch: "#c97b4a" },
  ice: { label: "Ice", swatch: "#6e9fc4" },
  periwinkle: { label: "Periwinkle", swatch: "#8b8fd9" },
  amber: { label: "Amber", swatch: "#e0b34a" },
} as const;

export type Ground = keyof typeof GROUNDS;
export type Accent = keyof typeof ACCENTS;
/**
 * Palette option A: any colour as the accent, stored in `accent` beside the
 * preset keys as a lowercase `#rrggbb`. It drives the action colour and the
 * lead chart series only; the status tokens (ok, warn, error) never move.
 */
export type CustomAccent = `#${string}`;
export type AccentChoice = Accent | CustomAccent;
export type Density = "compact" | "cards";
export type Corners = 0 | 2 | 4 | 10;

export interface Appearance {
  ground: Ground;
  accent: AccentChoice;
  density: Density;
  corners: Corners;
}

export const DEFAULT_APPEARANCE: Appearance = {
  ground: "black",
  accent: "green",
  density: "compact",
  corners: 2,
};

/**
 * Named combinations, so the picker offers a decision rather than four
 * dropdowns. `ground` implies light or dark, so choosing a theme also flips
 * the colour mode — handled by the caller, which owns the next-themes setter.
 */
export const THEMES = [
  { id: "orbit", name: "Orbit", hint: "Black · green", ground: "black", accent: "green" },
  { id: "forge", name: "Forge", hint: "Black · copper", ground: "black", accent: "copper" },
  { id: "console", name: "Console", hint: "Charcoal · ice", ground: "charcoal", accent: "ice" },
  {
    id: "dusk",
    name: "Dusk",
    hint: "Charcoal · periwinkle",
    ground: "charcoal",
    accent: "periwinkle",
  },
  { id: "field", name: "Field", hint: "Emerald · green", ground: "emerald", accent: "green" },
  { id: "daylight", name: "Daylight", hint: "Mist · green", ground: "mist", accent: "green" },
  { id: "workshop", name: "Workshop", hint: "Paper · copper", ground: "paper", accent: "copper" },
] as const satisfies ReadonlyArray<{
  id: string;
  name: string;
  hint: string;
  ground: Ground;
  accent: Accent;
}>;

/**
 * Org-level policy. An admin can set a house theme, and optionally lock it so
 * the personal picker becomes read-only — the same shape as every other
 * org-default-plus-override policy in the platform.
 *
 * The fields are not served yet (see #135); `resolveAppearance` already
 * honours them so the UI and the precedence rule land together with the
 * backend rather than after it. Until then callers pass `null` and every user
 * gets the personal layer.
 */
export interface OrgAppearancePolicy {
  /** Applied to anyone who has not chosen for themselves. */
  orgDefault?: Partial<Appearance> | null;
  /** When true the org default wins outright and personal choice is ignored. */
  locked?: boolean;
}

export interface ResolvedAppearance {
  value: Appearance;
  /** True when the org locked the theme — the picker renders disabled. */
  locked: boolean;
}

/**
 * Precedence: a locked org theme beats everything; otherwise a personal
 * choice beats the org default, which beats the shipped default.
 */
export function resolveAppearance(
  personal: Partial<Appearance> | null,
  policy: OrgAppearancePolicy | null
): ResolvedAppearance {
  const orgDefault = policy?.orgDefault ?? null;
  if (policy?.locked) {
    return { value: normalize({ ...DEFAULT_APPEARANCE, ...orgDefault }), locked: true };
  }
  return {
    value: normalize({ ...DEFAULT_APPEARANCE, ...orgDefault, ...(personal ?? {}) }),
    locked: false,
  };
}

export const STORAGE_KEY = "astrolift.appearance";

function isGround(v: unknown): v is Ground {
  return typeof v === "string" && v in GROUNDS;
}
/** One of the preset accents (the only kind an org house theme can hold today). */
export function isAccent(v: unknown): v is Accent {
  return typeof v === "string" && v in ACCENTS;
}

const HEX = /^#[0-9a-f]{6}$/;

/** A well-formed custom accent: `#rrggbb`, lowercase. */
export function isCustomAccent(v: unknown): v is CustomAccent {
  return typeof v === "string" && HEX.test(v);
}

/** `#RGB`, `rrggbb` or `#RRGGBB` as a stored custom accent, or null. */
export function parseCustomAccent(raw: string): CustomAccent | null {
  let v = raw.trim().toLowerCase();
  if (!v.startsWith("#")) v = `#${v}`;
  if (/^#[0-9a-f]{3}$/.test(v)) v = `#${v[1]}${v[1]}${v[2]}${v[2]}${v[3]}${v[3]}`;
  return isCustomAccent(v) ? v : null;
}

/**
 * The page ground each `data-ground` paints (`--background` in globals.css,
 * the oklch grounds converted to sRGB), for the contrast check below.
 */
export const GROUND_BACKGROUND: Record<Ground, string> = {
  black: "#000000",
  charcoal: "#0e0f10",
  emerald: "#050b08",
  paper: "#faf6ee",
  mist: "#f2f7f9",
};

/** WCAG 2.2 §1.4.11: a control has to stand 3:1 off what it sits on. */
export const MIN_ACCENT_CONTRAST = 3;

function luminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** The WCAG contrast ratio between two `#rrggbb` colours, 1 to 21. */
export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** A custom accent's contrast against a ground, and whether it clears the bar. */
export function accentContrast(accent: CustomAccent, ground: Ground) {
  const ratio = contrastRatio(accent, GROUND_BACKGROUND[ground]);
  return { ratio, ok: ratio >= MIN_ACCENT_CONTRAST };
}

/** Black or white, whichever reads better on a filled accent (always 4.5:1 or more). */
export function inkFor(accent: CustomAccent): "#000000" | "#ffffff" {
  return contrastRatio(accent, "#000000") >= contrastRatio(accent, "#ffffff")
    ? "#000000"
    : "#ffffff";
}

function isAccentChoice(v: unknown): v is AccentChoice {
  return isAccent(v) || isCustomAccent(v);
}

/** Coerce anything (stale storage, a hand-edited value) to a valid preference. */
export function normalize(raw: unknown): Appearance {
  const v = (typeof raw === "object" && raw !== null ? raw : {}) as Record<string, unknown>;
  const ground = isGround(v.ground) ? v.ground : DEFAULT_APPEARANCE.ground;
  // A custom accent is checked against the ground it lands on, so moving to a
  // ground it cannot be read on falls back to the default instead of
  // painting controls nobody can see.
  const accent =
    isAccent(v.accent) || (isCustomAccent(v.accent) && accentContrast(v.accent, ground).ok)
      ? v.accent
      : DEFAULT_APPEARANCE.accent;
  return {
    ground,
    accent,
    density: v.density === "cards" ? "cards" : DEFAULT_APPEARANCE.density,
    corners: ([0, 2, 4, 10] as const).find((c) => c === v.corners) ?? DEFAULT_APPEARANCE.corners,
  };
}

/**
 * Keep only the axes present and valid, without filling in the rest.
 *
 * Distinct from `normalize`, which always returns a complete preference.
 * A partial has to stay partial: an org that pins only the accent must not
 * thereby also pin ground, density and corners to the shipped defaults, and
 * a stored personal preference of `{accent}` must leave the org free to
 * decide the other three.
 */
export function normalizePartial(raw: unknown): Partial<Appearance> {
  const v = (typeof raw === "object" && raw !== null ? raw : {}) as Record<string, unknown>;
  const out: Partial<Appearance> = {};
  if (isGround(v.ground)) out.ground = v.ground;
  if (isAccentChoice(v.accent)) out.accent = v.accent;
  if (v.density === "cards" || v.density === "compact") out.density = v.density;
  const corners = ([0, 2, 4, 10] as const).find((c) => c === v.corners);
  if (corners !== undefined) out.corners = corners;
  return out;
}

/**
 * The stored personal preference, or `null` when this person has never
 * chosen.
 *
 * Returning `null` rather than a filled-in default is load-bearing: an org
 * default only applies to people who haven't chosen, and if "never chose"
 * were indistinguishable from "chose the defaults" the org default would
 * never apply to anyone — a policy that reads as coverage while covering
 * nothing.
 */
export function readAppearance(): Partial<Appearance> | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = normalizePartial(JSON.parse(raw));
    return Object.keys(parsed).length > 0 ? parsed : null;
  } catch {
    // private mode, disabled storage, or malformed JSON — treat as unset
    return null;
  }
}

/**
 * The person's appearance as the server holds it (#2154): the same partial,
 * or `null` when they have never chosen (an empty object). It replaces the
 * browser copy when signed in.
 */
export function appearanceFromServer(raw: unknown): Partial<Appearance> | null {
  const parsed = normalizePartial(raw);
  return Object.keys(parsed).length > 0 ? parsed : null;
}

export function writeAppearance(next: Partial<Appearance>): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // preference just won't persist; the applied theme is still correct
  }
}

/** Forget the personal preference entirely, handing the decision back to
 * the org default (or the shipped one). */
export function clearAppearance(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // nothing to do — the applied theme is still corrected by the caller
  }
}

/** The tokens a custom accent paints: the action colour in both modes (charts follow it). */
export const CUSTOM_ACCENT_TOKENS = ["--brand-primary", "--brand-primary-dark"] as const;

/** Stamp the axes onto <html>. Safe to call before React hydrates. */
export function applyAppearance(next: Appearance, root?: HTMLElement): void {
  const el = root ?? document.documentElement;
  el.dataset.ground = next.ground;
  if (isCustomAccent(next.accent)) {
    // The preset accents are CSS blocks keyed on data-accent; a custom one
    // has no block, so it sets the same few tokens inline instead.
    el.dataset.accent = "custom";
    for (const token of CUSTOM_ACCENT_TOKENS) el.style.setProperty(token, next.accent);
    el.style.setProperty("--brand-on-primary", inkFor(next.accent));
    el.style.setProperty("--primary-foreground", inkFor(next.accent));
  } else {
    el.dataset.accent = next.accent;
    for (const token of [...CUSTOM_ACCENT_TOKENS, "--brand-on-primary", "--primary-foreground"]) {
      el.style.removeProperty(token);
    }
  }
  el.dataset.density = next.density;
  el.style.setProperty("--radius", `${next.corners}px`);
}

/** Whether a ground belongs to the light or the dark colour mode. */
export function modeFor(ground: Ground): "light" | "dark" {
  return GROUNDS[ground].mode;
}
