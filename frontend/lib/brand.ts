/**
 * Astrolift brand tokens — the canonical palette shared with the
 * marketing site (astrolift.ai) and docs site (astrolift.dev).
 *
 * The runtime source of truth for *styling* is the CSS custom
 * properties in app/globals.css (`--brand-primary`, etc.). This module
 * mirrors those values for code paths that need raw strings — chart
 * series colours, OG image generators, Sentry tag values, etc. —
 * without parsing CSS.
 *
 * Single-tenant: one brand per install. Per-org overrides are not
 * planned (see #269).
 */

export const brand = {
  name: "Astrolift",
  primary: "#17805c",
  primaryDark: "#1ea97b",
  onPrimary: "#04241b",
  emerald: {
    bg: "#04231a",
    codeBg: "#052b25",
    surfaceDeep: "#053438",
    card: "#0a5a46",
  },
  fg: "#eaf6ef",
  fgMuted: "#8ba199",
} as const;

export type Brand = typeof brand;
