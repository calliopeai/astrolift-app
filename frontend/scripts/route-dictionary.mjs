#!/usr/bin/env node
// Route dictionary generator — emits frontend/ROUTES.md.
//
// Walks app/(app)/**/page.tsx and classifies every route:
//   page             — a real surface
//   redirect → /x    — a thin server alias (page.tsx calls redirect())
//   flagged          — parked via lib/route-flags.ts (on disk, 404s)
//
// Cross-references the nav-chrome surfaces (components/AstroliftNav.tsx's
// module switcher, NavTree.tsx, NavUser.tsx, KeyboardShortcuts.tsx) and
// components/CommandPalette.tsx so the table shows where each route is
// reachable from. Output is deterministic (sorted, no timestamps) — run
// it twice, get the same bytes.
//
// Usage: npm run routes:dict   (writes frontend/ROUTES.md)

import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, relative, resolve, dirname, sep } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const APP_DIR = join(FRONTEND, "app", "(app)");
const FLAGS_FILE = join(FRONTEND, "lib", "route-flags.ts");
const NAV_FILE = join(FRONTEND, "components", "AstroliftNav.tsx");
const PALETTE_FILE = join(FRONTEND, "components", "CommandPalette.tsx");
const NAV_TREE_FILE = join(FRONTEND, "components", "NavTree.tsx");
const NAV_USER_FILE = join(FRONTEND, "components", "NavUser.tsx");
const KEYBOARD_FILE = join(FRONTEND, "components", "KeyboardShortcuts.tsx");
const OUT_FILE = join(FRONTEND, "ROUTES.md");

// ─── collect app/(app)/**/page.tsx ─────────────────────────────────────────

function walkPages(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) =>
    a.name.localeCompare(b.name)
  )) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walkPages(full));
    else if (entry.name === "page.tsx") out.push(full);
  }
  return out;
}

/** app/(app)/foo/[slug]/page.tsx → /foo/[slug]; group segments drop. */
function routeOf(pageFile) {
  const segments = relative(APP_DIR, dirname(pageFile))
    .split(sep)
    .filter((s) => s && !(s.startsWith("(") && s.endsWith(")")));
  return "/" + segments.join("/");
}

// ─── route flags ────────────────────────────────────────────────────────────

function parseFlags() {
  const src = readFileSync(FLAGS_FILE, "utf8");
  const body = src.match(/ROUTE_FLAGS[^=]*=\s*\{([\s\S]*?)\}/)?.[1] ?? "";
  const flags = new Map();
  for (const m of body.matchAll(/"([^"]+)":\s*(true|false)/g)) {
    flags.set(m[1], m[2] === "true");
  }
  return flags;
}

function flagFor(route, flags) {
  for (const [prefix, enabled] of flags) {
    if (!enabled && (route === prefix || route.startsWith(prefix + "/"))) return prefix;
  }
  return null;
}

// ─── nav-surface hrefs ──────────────────────────────────────────────────────

/**
 * Pull href strings out of a nav component. For AstroliftNav, only the
 * live module switcher counts — the file keeps flag-off deferred section
 * definitions above the `const modules` marker for re-enable reference,
 * and those must not read as "on nav".
 */
function hrefsOf(file, { fromMarker } = {}) {
  let src = readFileSync(file, "utf8");
  if (fromMarker) {
    const at = src.indexOf(fromMarker);
    if (at !== -1) src = src.slice(at);
  }
  const out = new Set();
  for (const m of src.matchAll(/href(?::\s*|=)"([^"]+)"/g)) {
    out.add(m[1].split("#")[0]); // fragment-insensitive
  }
  return out;
}

// ─── emit ───────────────────────────────────────────────────────────────────

const flags = parseFlags();
// Every sidebar / nav-chrome surface that emits route hrefs counts as a
// "nav" home: the module switcher (AstroliftNav), the workspace tree
// (NavTree), the user menu (NavUser), and the keyboard-shortcut map
// (KeyboardShortcuts). Only literal string hrefs are captured — template
// -literal detail links (e.g. /teams/${slug}) are excluded by construction.
const navHrefs = new Set([
  ...hrefsOf(NAV_FILE, { fromMarker: "const modules" }),
  ...hrefsOf(NAV_TREE_FILE),
  ...hrefsOf(NAV_USER_FILE),
  ...hrefsOf(KEYBOARD_FILE),
]);
const paletteHrefs = hrefsOf(PALETTE_FILE);

const rows = walkPages(APP_DIR)
  .map((file) => {
    const route = routeOf(file);
    const src = readFileSync(file, "utf8");
    // Redirect target: the first leading-slash path inside the redirect()
    // call. Handles both the literal form redirect("/x") and the
    // query-forwarding conditional redirect(qs ? `/x?${qs}` : "/x") — the
    // `?${…}` tail is stripped so the table shows the bare destination.
    const redirectAt = src.indexOf("redirect(");
    const redirectTarget =
      redirectAt === -1
        ? null
        : (src.slice(redirectAt, redirectAt + 200).match(/["'`](\/[^"'`?]*)/)?.[1] ?? null);
    const flag = flagFor(route, flags);

    let kind = "page";
    if (redirectTarget) kind = `redirect → \`${redirectTarget}\``;
    else if (flag) kind = "flagged";

    const homes = [];
    if (navHrefs.has(route)) homes.push("nav");
    if (paletteHrefs.has(route)) homes.push("palette");

    const notes = [];
    if (flag) notes.push(`off via \`${flag}\` in lib/route-flags.ts`);
    if (redirectTarget) notes.push("thin server alias");

    return {
      route,
      kind,
      home: homes.join(" + ") || "—",
      notes: notes.join("; ") || "",
    };
  })
  .sort((a, b) => a.route.localeCompare(b.route));

const lines = [
  "# Route dictionary",
  "",
  "Generated by `npm run routes:dict` (scripts/route-dictionary.mjs) — do not hand-edit.",
  "",
  `Covers every \`page.tsx\` under \`app/(app)/\` (${rows.length} routes). "Nav home" says`,
  "which chrome surface links to the route: the sidebar nav chrome",
  "(components/AstroliftNav.tsx, NavTree.tsx, NavUser.tsx,",
  "KeyboardShortcuts.tsx) and/or the Cmd-K palette",
  "(components/CommandPalette.tsx). Routes reachable only by in-page links or",
  "direct URL show `—`.",
  "",
  "| Route | Kind | Nav home | Notes |",
  "| --- | --- | --- | --- |",
  ...rows.map((r) => `| \`${r.route}\` | ${r.kind} | ${r.home} | ${r.notes} |`),
  "",
];

writeFileSync(OUT_FILE, lines.join("\n"));
console.log(`route-dictionary: wrote ${relative(process.cwd(), OUT_FILE)} (${rows.length} routes)`);
