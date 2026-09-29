import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

/**
 * One list per screen (Leo's list rule 3, 2026-09-28): when a screen has a
 * list, the list is the main thing. A second list gets its own tab, section
 * or route, or on an overview a `ListSummary` (which does not count here).
 *
 * Static, like story-coverage.test.ts: every screen file
 * (`components/screens/**\/*Screen.tsx` and `*Tab*.tsx`) is parsed and its
 * lists counted. A list is a `<ListPage>` (full or embedded) or a
 * `<DataTable>`: the second is a list the list-archetype lint has not moved
 * to ListPage yet, and two of them are still two lists. Branches that never
 * render together are not added up: `a ? <ListPage embedded/> : <ListPage/>`
 * is one list, and so is an early `if (…) return <ListPage/>`.
 *
 * Report-only for the files below, today's violators, each with a reason,
 * so the apply agents can shrink it: a listed file that now has one list
 * fails until its entry is deleted, and a new violator fails outright.
 */
const ALLOWLIST: { file: string; reason: string }[] = [];

const ROOT = path.resolve(__dirname);
const LIST_TAGS = new Set(["ListPage", "DataTable"]);

function screenFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return screenFiles(full);
    if (/\.(stories|test)\.tsx$/.test(name)) return [];
    return /(Screen\.tsx|Tab[^/]*\.tsx)$/.test(name) ? [full] : [];
  });
}

function tagName(node: ts.JsxOpeningLikeElement): string {
  return node.tagName.getText();
}

/** True when a statement always returns (a `return`, or a block ending in one). */
function returns(stmt: ts.Statement): boolean {
  if (ts.isReturnStatement(stmt)) return true;
  if (ts.isBlock(stmt)) {
    const last = stmt.statements[stmt.statements.length - 1];
    return last !== undefined && returns(last);
  }
  return false;
}

/**
 * Counts the lists one node renders at once. `local` maps the file's own
 * components to their bodies, so `<AttachedBundlesPanel />` inside a screen
 * brings its table with it; a slot passed in as a prop is another file's.
 */
function counter(local: Map<string, ts.Node>) {
  const visiting = new Set<string>();

  function countStatements(stmts: readonly ts.Statement[]): number {
    let sum = 0;
    for (let i = 0; i < stmts.length; i++) {
      const stmt = stmts[i]!;
      // `if (x) return <A/>; …rest` renders A or the rest, never both.
      if (ts.isIfStatement(stmt) && !stmt.elseStatement && returns(stmt.thenStatement)) {
        const branch = count(stmt.expression) + count(stmt.thenStatement);
        return sum + Math.max(branch, countStatements(stmts.slice(i + 1)));
      }
      sum += count(stmt);
    }
    return sum;
  }

  function count(node: ts.Node): number {
    if (ts.isConditionalExpression(node)) {
      return count(node.condition) + Math.max(count(node.whenTrue), count(node.whenFalse));
    }
    if (ts.isIfStatement(node)) {
      return (
        count(node.expression) +
        Math.max(count(node.thenStatement), node.elseStatement ? count(node.elseStatement) : 0)
      );
    }
    if (ts.isBlock(node) || ts.isSourceFile(node)) return countStatements(node.statements);
    let own = 0;
    if (ts.isJsxSelfClosingElement(node) || ts.isJsxOpeningElement(node)) {
      const tag = tagName(node);
      if (LIST_TAGS.has(tag)) own = 1;
      const body = local.get(tag);
      if (body && !visiting.has(tag)) {
        visiting.add(tag);
        own += count(body);
        visiting.delete(tag);
      }
    }
    let children = 0;
    node.forEachChild((child) => {
      children += count(child);
    });
    return own + children;
  }

  return { count, visiting };
}

/** The file's top-level components: `function Name` and `const Name = (…) =>`. */
function components(file: ts.SourceFile): Map<string, ts.Node> {
  const out = new Map<string, ts.Node>();
  for (const stmt of file.statements) {
    if (ts.isFunctionDeclaration(stmt) && stmt.name && /^[A-Z]/.test(stmt.name.text) && stmt.body) {
      out.set(stmt.name.text, stmt.body);
    }
    if (ts.isVariableStatement(stmt)) {
      for (const decl of stmt.declarationList.declarations) {
        const init = decl.initializer;
        if (
          ts.isIdentifier(decl.name) &&
          /^[A-Z]/.test(decl.name.text) &&
          init &&
          (ts.isArrowFunction(init) || ts.isFunctionExpression(init))
        ) {
          out.set(decl.name.text, init.body);
        }
      }
    }
  }
  return out;
}

/** The most lists any one component in the file renders at once. */
function listsInSource(fileName: string, src: string): number {
  const file = ts.createSourceFile(fileName, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const local = components(file);
  const { count, visiting } = counter(local);
  if (local.size === 0) return count(file);
  return Math.max(
    ...[...local].map(([name, body]) => {
      visiting.add(name);
      const n = count(body);
      visiting.delete(name);
      return n;
    })
  );
}

function listsIn(file: string): number {
  const src = readFileSync(file, "utf8");
  if (!/<(ListPage|DataTable)\b/.test(src)) return 0;
  return listsInSource(file, src);
}

describe("one list per screen", () => {
  const counts = screenFiles(ROOT).map((full) => ({
    file: path.relative(ROOT, full).split(path.sep).join("/"),
    lists: listsIn(full),
  }));
  const allowed = new Map(ALLOWLIST.map((e) => [e.file, e.reason]));

  it("renders at most one ListPage or DataTable per screen file", () => {
    const violators = counts.filter((c) => c.lists > 1 && !allowed.has(c.file));
    expect(
      violators,
      "Screens with more than one list. Move the second list to its own tab, section or " +
        "route, or make it a ListSummary (an overview) or a Feed (a stream)."
    ).toEqual([]);
  });

  it("reports the allowlisted screens, and drops them once they have one list", () => {
    const over = counts.filter((c) => c.lists > 1 && allowed.has(c.file));
    for (const c of over) {
      console.info(
        `one-list (report only): ${c.file} has ${c.lists} lists: ${allowed.get(c.file)}`
      );
    }
    const stale = ALLOWLIST.map((e) => e.file).filter(
      (f) => !counts.some((c) => c.file === f && c.lists > 1)
    );
    expect(stale, "Allowlisted screens that now have one list: delete their entries.").toEqual([]);
  });

  it("gives every entry a reason", () => {
    for (const entry of ALLOWLIST) {
      expect(entry.reason.trim().length, `${entry.file} has no usable reason`).toBeGreaterThan(30);
    }
  });

  it("counts branches that never render together once", () => {
    const parse = (src: string) => listsInSource("x.tsx", src);
    expect(parse("function S() { return e ? <ListPage embedded /> : <ListPage />; }")).toBe(1);
    expect(parse("function S() { if (x) return <ListPage />; return <ListPage />; }")).toBe(1);
    expect(parse("function S() { return <div><ListPage /><DataTable /></div>; }")).toBe(2);
    expect(parse("function S() { return <div><ListPage /><ListSummary /></div>; }")).toBe(1);
  });

  it("follows the file's own components, not the ones passed in", () => {
    const parse = (src: string) => listsInSource("x.tsx", src);
    // A screen and its panel: two lists on one page.
    expect(
      parse(
        "function S() { return <div><DataTable /><P /></div>; } function P() { return <DataTable />; }"
      )
    ).toBe(2);
    // Two tab views in one file, each shown alone behind a slot: one each.
    expect(
      parse(
        "function S({ a }) { return <div>{a}</div>; } function A() { return <DataTable />; } const B = () => <DataTable />;"
      )
    ).toBe(1);
  });
});
