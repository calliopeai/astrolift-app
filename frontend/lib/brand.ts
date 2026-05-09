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
  primary: "#08d4b8",
  primaryDark: "#08d4b8",
  onPrimary: "#0b0b19",
  navy: {
    900: "#010409",
    800: "#090c10",
    700: "#0d1117",
    50: "#1a2035",
  },
  fg: "#e0e5f2",
  fgMuted: "#a3aed0",
} as const;

export type Brand = typeof brand;
