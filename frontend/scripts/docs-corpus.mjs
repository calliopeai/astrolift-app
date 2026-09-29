#!/usr/bin/env node
// Documentation corpus generator — emits frontend/DOCS.md and the packaged
// backend copy.
//
// The wayfinding assistant (#1101) answers "how do I ..." from the product's
// own documentation. The epic assumed that corpus was "already in-repo,
// structured"; it is not. Every page under app/(app)/documentation/ is a TSX
// component, so the prose is interleaved with JSX, className strings and
// entity escapes. Feeding a model raw TSX would spend most of the prompt on
// Tailwind classes and would let a class name be read as content.
//
// So this extracts the text a reader actually sees: headings become markdown
// headings, paragraphs and list items become lines, and everything
// structural is dropped. Deterministic (sorted, no timestamps) — run it
// twice, get the same bytes — because a CI gate diffs the output.
//
// Two outputs on purpose. The backend image contains backend/ only, so the
// assistant cannot read a sibling frontend/ path at runtime; it reads the
// packaged copy. Writing both here means the frontend lint job's
// `git diff --exit-code` catches drift in either, which matters because that
// job is the only one that runs when a doc page is the sole change.
//
// Usage: npm run docs:corpus   (writes frontend/DOCS.md + the backend copy)

import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, relative, resolve, dirname, sep } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DOCS_DIR = join(FRONTEND, "app", "(app)", "documentation");
const SCREENS_DIR = join(FRONTEND, "components", "screens", "documentation");
const OUT_FILE = join(FRONTEND, "DOCS.md");
const PACKAGED = join(FRONTEND, "..", "backend", "astrolift_agents", "data", "docs.md");

// ─── collect documentation/**/*.tsx ────────────────────────────────────────

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) =>
    a.name.localeCompare(b.name)
  )) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else if (entry.name.endsWith(".tsx") && entry.name !== "layout.tsx") out.push(full);
  }
  return out;
}

function routeFor(file) {
  const rel = relative(DOCS_DIR, dirname(file)).split(sep).filter(Boolean);
  return "/documentation" + (rel.length ? "/" + rel.join("/") : "");
}

// ─── extract the visible prose ─────────────────────────────────────────────

const ENTITIES = {
  "&apos;": "'",
  "&quot;": '"',
  "&amp;": "&",
  "&lt;": "<",
  "&gt;": ">",
  "&mdash;": "—",
  "&nbsp;": " ",
};

// Headings carry the structure a reader navigates by, so they survive as
// markdown rather than being flattened into the body.
const HEADING = { h1: "#", h2: "##", h3: "###", h4: "####" };

function decode(text) {
  return text.replace(/&[a-z]+;/g, (e) => ENTITIES[e] ?? e);
}

function textOf(chunk) {
  return decode(
    chunk
      // `{" "}` and friends are JSX whitespace carriers, not content, but
      // they sit *between* words: dropping them without a space rejoins
      // "work through" and "Cluster prerequisites" into one token.
      .replace(/\{[^{}]*\}/g, " ")
      .replace(/<[^>]*>/g, " ")
      .replace(/\s+/g, " ")
      .trim()
  );
}

function extract(src) {
  const out = [];
  // Only the returned JSX. Anything above it is imports and metadata.
  const body = src.slice(src.indexOf("return ("));

  const tag = /<(h1|h2|h3|h4|p|li)\b[^>]*>([\s\S]*?)<\/\1>/g;
  let match;
  while ((match = tag.exec(body)) !== null) {
    const [, name, inner] = match;
    const text = textOf(inner);
    if (!text) continue;
    if (HEADING[name]) out.push(`${HEADING[name]} ${text}`);
    else if (name === "li") out.push(`- ${text}`);
    else out.push(text);
  }
  return out;
}

// A page whose whole body is `redirect(...)` holds no prose by construction.
function isRedirect(src) {
  return /\bredirect\(/.test(src) && !/<(h1|h2|p)\b/.test(src);
}

// Grouped by route, not by file: a route can be served by more than one
// component (page.tsx plus its -client.tsx), and a per-file model both
// splits that page's prose and lists its own route as contributing nothing.
// Routes only render a screen (the Storybook-first rule), so a page's prose
// lives in the documentation screen it imports. Follow those imports; a
// module imported by more than one route is shared chrome (the docs shell,
// a nav), not that page's content, and is skipped.
const SCREEN_IMPORT = /from\s+"@\/components\/screens\/documentation\/([^"]+)"/g;
function screenModules(src) {
  return [...src.matchAll(SCREEN_IMPORT)].map((m) => m[1]);
}
function screenSource(mod) {
  for (const ext of [".tsx", ".ts"]) {
    try {
      return readFileSync(join(SCREENS_DIR, mod + ext), "utf8");
    } catch {
      // Not this extension.
    }
  }
  return "";
}
const docFiles = walk(DOCS_DIR).filter((file) => !routeFor(file).includes("["));
const importCount = new Map();
for (const file of docFiles) {
  for (const mod of new Set(screenModules(readFileSync(file, "utf8")))) {
    importCount.set(mod, (importCount.get(mod) ?? 0) + 1);
  }
}

const byRoute = new Map();
for (const file of docFiles) {
  // A dynamic segment is not a citable destination -- "/documentation/
  // tutorials/[slug]" is not somewhere anyone can be sent, and the prose
  // around it is template chrome rather than content.
  const route = routeFor(file);
  const src = readFileSync(file, "utf8");
  const entry = byRoute.get(route) ?? { route, lines: [], redirect: true };
  entry.lines.push(...extract(src));
  for (const mod of screenModules(src)) {
    if (importCount.get(mod) === 1) entry.lines.push(...extract(screenSource(mod)));
  }
  // Only a route whose every file is a bare redirect is one.
  if (!isRedirect(src)) entry.redirect = false;
  byRoute.set(route, entry);
}

const all = [...byRoute.values()].sort((a, b) => a.route.localeCompare(b.route));
const pages = all.filter((p) => p.lines.length > 0);

// A page that contributes nothing and is not a redirect is worth saying out
// loud rather than silently omitting: it means the extractor stopped
// matching that page's markup, and the assistant would answer "there's
// nothing about that" with no way for anyone to notice the corpus had
// quietly shrunk. Redirects are listed separately because they hold no
// prose by construction, and filing them as extraction failures would send
// the next reader hunting a bug that is not there.
const empty = all.filter((p) => p.lines.length === 0 && !p.redirect).map((p) => p.route);
const redirects = all.filter((p) => p.lines.length === 0 && p.redirect).map((p) => p.route);

const lines = [
  "# Documentation corpus",
  "",
  "Generated by `npm run docs:corpus` (scripts/docs-corpus.mjs) — do not hand-edit.",
  "",
  `The reader-visible prose of every documentation page (${pages.length} pages).`,
  "Grounding for the wayfinding assistant; each section is headed by the route",
  "that serves it, so an answer can cite where to go as well as what it says.",
  "",
  ...(redirects.length
    ? [
        `Redirecting off-platform (${redirects.length}, no prose to extract): ${redirects.join(", ")}.`,
        "",
      ]
    : []),
  ...(empty.length
    ? [
        `Contributing no extracted prose (${empty.length}): ${empty.join(", ")}.`,
        "Not redirects, so the extractor has stopped matching these pages'",
        "markup and the corpus has silently shrunk. Fix that before trusting",
        "an answer that says a topic is undocumented.",
        "",
      ]
    : []),
  ...pages.flatMap((p) => [`## ${p.route}`, "", ...p.lines, ""]),
];

const text = lines.join("\n");
writeFileSync(OUT_FILE, text);
writeFileSync(PACKAGED, text);
console.log(
  `docs-corpus: wrote ${relative(process.cwd(), OUT_FILE)} and the packaged copy (${pages.length} pages, ${redirects.length} redirects, ${empty.length} unmatched)`
);
