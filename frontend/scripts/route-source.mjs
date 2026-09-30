import ts from "typescript";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

/** Distinguish unconditional compatibility aliases from real pages with
 * conditional redirects. Looking for strings also misreads docs/examples. */
export function redirectOf(file, source) {
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let call;
  let factory;
  let renders = false;
  function inspect(node) {
    if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node) || ts.isJsxFragment(node))
      renders = true;
    if (
      ts.isCallExpression(node) &&
      ts.isIdentifier(node.expression) &&
      node.expression.text === "redirect"
    )
      call ??= node;
    if (
      ts.isExportAssignment(node) &&
      ts.isCallExpression(node.expression) &&
      ts.isIdentifier(node.expression.expression)
    )
      factory = node.expression.expression.text;
    ts.forEachChild(node, inspect);
  }
  inspect(tree);
  if (renders) return null;
  if (!call && factory) {
    for (const statement of tree.statements) {
      if (!ts.isImportDeclaration(statement) || !ts.isStringLiteral(statement.moduleSpecifier))
        continue;
      const imports = statement.importClause?.namedBindings;
      if (
        !imports ||
        !ts.isNamedImports(imports) ||
        !imports.elements.some((entry) => entry.name.text === factory)
      )
        continue;
      const importPath = statement.moduleSpecifier.text;
      if (!importPath.startsWith(".")) continue;
      const stem = resolve(dirname(file), importPath);
      const dependency = [stem + ".ts", stem + ".tsx"].find((candidate) => existsSync(candidate));
      if (
        dependency &&
        dependency !== file &&
        redirectOf(dependency, readFileSync(dependency, "utf8"))
      )
        return { target: null };
    }
  }
  if (!call) return null;
  const text = call.arguments[0]?.getText(tree) ?? "";
  const target = text.match(/["'`]((?:\/|https?:\/\/)[^"'`?]*)/)?.[1] ?? null;
  return { target };
}
