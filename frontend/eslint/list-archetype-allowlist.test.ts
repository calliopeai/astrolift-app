import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { LIST_ARCHETYPE_ALLOWLIST } from "./list-archetype-allowlist.mjs";

/**
 * The list-archetype allowlist cannot drift (see raw-table-allowlist.test.ts
 * for why): two-way set equality against the tree. A stale entry is a
 * surface that moved to ListPage, ListSummary or Feed and left its exemption
 * behind; an unlisted one renders DataTable without a stated blocker.
 *
 * Imports are read with the TypeScript parser, as the lint rule reads them,
 * so a DataTable import quoted inside a string (the tutorials' code samples)
 * and a type-only import do not count.
 */

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SEARCH_ROOTS = ["app", "components"];
const SKIP_DIRS = new Set(["node_modules", ".next", "__generated__"]);
// The primitives built on DataTable; the rule's config ignores them.
const RULE_EXEMPT_DIRS = ["components/data-table/", "components/list/", "components/feed/"];

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(path.join(FRONTEND, dir), { withFileTypes: true })) {
    const rel = `${dir}/${entry.name}`;
    if (entry.isDirectory()) {
      if (!SKIP_DIRS.has(entry.name)) walk(rel, out);
    } else if (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx")) {
      out.push(rel);
    }
  }
  return out;
}

/** True when the file imports the `DataTable` value from @/components/data-table. */
function importsDataTable(fileName: string, src: string): boolean {
  if (!src.includes("DataTable")) return false;
  const file = ts.createSourceFile(fileName, src, ts.ScriptTarget.Latest, false);
  return file.statements.some((stmt) => {
    if (!ts.isImportDeclaration(stmt) || !ts.isStringLiteral(stmt.moduleSpecifier)) return false;
    const source = stmt.moduleSpecifier.text;
    if (source !== "@/components/data-table" && !source.startsWith("@/components/data-table/")) {
      return false;
    }
    const clause = stmt.importClause;
    if (!clause || clause.isTypeOnly) return false;
    const bindings = clause.namedBindings;
    if (!bindings || !ts.isNamedImports(bindings)) return false;
    return bindings.elements.some(
      (el) => !el.isTypeOnly && (el.propertyName ?? el.name).text === "DataTable"
    );
  });
}

function filesRenderingDataTable(): string[] {
  return SEARCH_ROOTS.flatMap((root) => walk(root))
    .filter((rel) => !RULE_EXEMPT_DIRS.some((dir) => rel.startsWith(dir)))
    .filter((rel) => importsDataTable(rel, readFileSync(path.join(FRONTEND, rel), "utf8")))
    .sort();
}

describe("list-archetype allowlist", () => {
  it("names exactly the files that still render DataTable directly", () => {
    const listed = [...LIST_ARCHETYPE_ALLOWLIST.map((e) => e.file)].sort();
    const actual = filesRenderingDataTable();

    const stale = listed.filter((f) => !actual.includes(f));
    const unlisted = actual.filter((f) => !listed.includes(f));

    expect(
      { stale, unlisted },
      "Allowlist out of step with the tree. `stale` entries no longer import " +
        "DataTable: delete them. `unlisted` files render DataTable without an " +
        "entry: move them to ListPage, ListSummary or Feed, or add an entry " +
        "stating the blocker."
    ).toEqual({ stale: [], unlisted: [] });
  });

  it("gives every entry a blocker rather than a bare path", () => {
    for (const entry of LIST_ARCHETYPE_ALLOWLIST) {
      expect(entry.reason.trim().length, `${entry.file} has no usable reason`).toBeGreaterThan(30);
    }
  });

  it("lists each file once", () => {
    const files = LIST_ARCHETYPE_ALLOWLIST.map((e) => e.file);
    expect(files).toEqual([...new Set(files)]);
  });

  it("reads imports, not strings or types", () => {
    expect(importsDataTable("a.tsx", 'import { DataTable } from "@/components/data-table";')).toBe(
      true
    );
    expect(
      importsDataTable("a.tsx", 'import { type Column, DataTable } from "@/components/data-table";')
    ).toBe(true);
    expect(
      importsDataTable("a.ts", 'import type { DataTable } from "@/components/data-table";')
    ).toBe(false);
    expect(
      importsDataTable(
        "a.ts",
        'const code = `import { DataTable } from "@/components/data-table";`;'
      )
    ).toBe(false);
  });
});
