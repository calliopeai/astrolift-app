import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { RAW_TABLE_ALLOWLIST } from "./raw-table-allowlist.mjs";

/**
 * The allowlist is only worth having if it cannot drift (#1243).
 *
 * A stale entry is the failure mode that matters: migrate a surface to
 * DataTable, leave its line behind, and the file quietly stops being a
 * ledger of outstanding work and starts being a blanket exemption for a
 * path — which is how the standard got ignored the first time. So the
 * assertion is two-way set equality against the tree, not "every entry
 * exists".
 */

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SEARCH_ROOTS = ["app", "components"];
const SKIP_DIRS = new Set(["node_modules", ".next", "__generated__"]);
// DataTable owns the primitives; the rule already exempts its directory.
const RULE_EXEMPT_DIR = "components/data-table/";
const RAW_TABLE_IMPORT = '"@/components/ui/table"';

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

function filesImportingThePrimitives(): string[] {
  return SEARCH_ROOTS.flatMap((root) => walk(root))
    .filter((rel) => !rel.startsWith(RULE_EXEMPT_DIR))
    .filter((rel) => readFileSync(path.join(FRONTEND, rel), "utf8").includes(RAW_TABLE_IMPORT))
    .sort();
}

describe("no-raw-table allowlist", () => {
  it("names exactly the files that still import the primitives", () => {
    const listed = [...RAW_TABLE_ALLOWLIST.map((e) => e.file)].sort();
    const actual = filesImportingThePrimitives();

    const stale = listed.filter((f) => !actual.includes(f));
    const unlisted = actual.filter((f) => !listed.includes(f));

    expect(
      { stale, unlisted },
      "Allowlist out of step with the tree. `stale` entries are surfaces that " +
        "no longer import `@/components/ui/table` — delete their entries. " +
        "`unlisted` files import the primitives without an entry — migrate them " +
        "to DataTable, or add an entry stating the blocker."
    ).toEqual({ stale: [], unlisted: [] });
  });

  it("gives every entry a blocker rather than a bare path", () => {
    for (const entry of RAW_TABLE_ALLOWLIST) {
      expect(entry.reason.trim().length, `${entry.file} has no usable reason`).toBeGreaterThan(30);
    }
  });

  it("lists each file once", () => {
    const files = RAW_TABLE_ALLOWLIST.map((e) => e.file);
    expect(files).toEqual([...new Set(files)]);
  });
});
