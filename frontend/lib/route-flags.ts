// Central kill-switch for built-but-parked routes (IA revamp, spec 36).
//
// A route listed here with `false` stays on disk but 404s: its page.tsx
// carries a one-line guard — `if (!isRouteEnabled("/route")) notFound();`
// — so nothing is deleted and restoring a surface is a one-word flip to
// `true` (plus re-adding its nav/palette entries). Prefix semantics: a
// key disables itself AND every sub-route ("/forms" covers "/forms/new",
// "/forms/[slug]", …).
//
// Currently parked:
//   /forms      — form builder demo surface (backend keeps accepting
//                 direct GraphQL submitForm calls; the FE never exposed
//                 a public submit page, so /forms/[slug]/submit parks
//                 with the rest of the tree).
//   /playground/topology and /playground/observability — unrelated demo showcases.
// Real prompt/history/starred routes use persisted endpoints and scoped local records.
export const ROUTE_FLAGS: Record<string, boolean> = {
  "/forms": false,
  "/playground": true,
  "/playground/topology": false,
  "/playground/observability": false,
};

/** True unless `path` (or a prefix of it) is flagged off in ROUTE_FLAGS. */
export function isRouteEnabled(path: string): boolean {
  for (const [prefix, enabled] of Object.entries(ROUTE_FLAGS)) {
    if (!enabled && (path === prefix || path.startsWith(prefix + "/"))) return false;
  }
  return true;
}
