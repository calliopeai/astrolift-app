#!/usr/bin/env node
// Route dictionary generator — emits frontend/ROUTES.md and the packaged
// backend copy the wayfinding assistant reads (#1101).
//
// Walks app/(app)/**/page.tsx and classifies every route:
//   page             — a real surface
//   redirect → /x    — a thin server alias (page.tsx calls redirect())
//   flagged          — parked via lib/route-flags.ts (on disk, 404s)
//
// Cross-references the nav-chrome surfaces (the rail model in
// lib/shell/nav-model.ts, the shell's user and org menus,
// KeyboardShortcuts.tsx) and
// components/CommandPalette.tsx so the table shows where each route is
// reachable from. Output is deterministic (sorted, no timestamps) — run
// it twice, get the same bytes.
//
// Usage: npm run routes:dict   (writes both copies)

import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, relative, resolve, dirname, sep } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const APP_DIR = join(FRONTEND, "app", "(app)");
const FLAGS_FILE = join(FRONTEND, "lib", "route-flags.ts");
const NAV_FILE = join(FRONTEND, "lib", "shell", "nav-model.ts");
const PALETTE_FILE = join(FRONTEND, "components", "CommandPalette.tsx");
const USER_MENU_FILE = join(FRONTEND, "components", "shell", "UserMenu.tsx");
const ORG_MENU_FILE = join(FRONTEND, "components", "shell", "OrgMenu.tsx");
const KEYBOARD_FILE = join(FRONTEND, "components", "KeyboardShortcuts.tsx");
const OUT_FILE = join(FRONTEND, "ROUTES.md");
// The backend serves this dictionary to the wayfinding assistant (#1101)
// and cannot read a sibling frontend/ path: its image contains backend/
// only. Written here rather than copied by hand so the frontend lint
// job's `git diff --exit-code` catches drift -- that job is the only one
// that runs when a route change touches nothing under backend/.
const PACKAGED = join(FRONTEND, "..", "backend", "astrolift_agents", "data", "routes.md");

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

/** Pull the literal href strings out of a nav file. */
function hrefsOf(file) {
  const src = readFileSync(file, "utf8");
  const out = new Set();
  for (const m of src.matchAll(/href(?::\s*|=)"([^"]+)"/g)) {
    out.add(m[1].split("#")[0]); // fragment-insensitive
  }
  return out;
}

// ─── emit ───────────────────────────────────────────────────────────────────

const flags = parseFlags();
// Every sidebar / nav-chrome surface that emits route hrefs counts as a
// "nav" home: the rail (nav-model), the user and org menus, and the
// keyboard-shortcut map (KeyboardShortcuts). Only literal string hrefs are captured — template
// -literal detail links (e.g. /teams/${slug}) are excluded by construction.
const navHrefs = new Set([
  ...hrefsOf(NAV_FILE),
  ...hrefsOf(USER_MENU_FILE),
  ...hrefsOf(ORG_MENU_FILE),
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
  "(lib/shell/nav-model.ts, components/shell/UserMenu.tsx, OrgMenu.tsx,",
  "KeyboardShortcuts.tsx) and/or the Cmd-K palette",
  "(components/CommandPalette.tsx). Routes reachable only by in-page links or",
  "direct URL show `—`.",
  "",
  "| Route | Kind | Nav home | Notes |",
  "| --- | --- | --- | --- |",
  ...rows.map((r) => `| \`${r.route}\` | ${r.kind} | ${r.home} | ${r.notes} |`),
  "",
];

const text = lines.join("\n");
writeFileSync(OUT_FILE, text);
writeFileSync(PACKAGED, text);
console.log(
  `route-dictionary: wrote ${relative(process.cwd(), OUT_FILE)} and the packaged copy (${rows.length} routes)`
);
