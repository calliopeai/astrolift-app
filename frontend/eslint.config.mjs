import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import prettierConfig from "eslint-config-prettier";

/**
 * Design-system guardrail (#1050 / epic #1046).
 *
 * Flags raw design values in `className` strings (and in `cva` / `tv`
 * variant definitions) so the token debt swept in #A4 cannot regrow:
 *   - raw hex / rgb() / hsl() colours   → use a semantic token
 *     (`text-success`, `bg-warning`, `border-danger`, `bg-info/10`, …)
 *   - arbitrary `text-[Npx]` sizes      → use the type scale (`text-2xs` … `text-2xl`)
 *   - arbitrary `rounded-[Npx]` radii   → use the radius tokens (`rounded-md` … `rounded-2xl`)
 *
 * Runs at `error` now that the #A4 debt is burnt down (#1050 close-out).
 * `var(--token)` / `calc()` / `min()`
 * arbitrary values are allowed (they already reference tokens). Escape hatch
 * for the unavoidable case (chart/SVG/terminal exact colours):
 *   // eslint-disable-next-line astrolift/no-raw-design-values
 */
const HEX = /#[0-9a-fA-F]{3,8}\b/;
const COLOR_FN = /\b(?:rgb|rgba|hsl|hsla)\(/;
const TEXT_SIZE = /(?<![\w-])text-\[[0-9.]+px\]/;
const ROUND_SIZE = /(?<![\w-])rounded-\[[0-9.]+px\]/;
const VARIANT_HELPERS = new Set(["cva", "tv"]);

/**
 * Data-surface guardrail (#1231 / epic #1230).
 *
 * The audit's verdict was that documentation alone demonstrably failed: a
 * DataTable composition layer was declared the house standard in
 * frontend/bootstrap.md and then sat at zero consumers for three months
 * while 100+ surfaces shipped their own tables. Nobody filed an issue.
 * Only a lint gate makes a standard hold.
 *
 *   - `no-raw-table` — `@/components/ui/table` is DataTable's private
 *     dependency. Import `DataTable` instead; a surface that reaches for
 *     the primitives is re-deriving pagination, empty states and loading
 *     behaviour that the component already gets right. Ships at `warn`
 *     until the wave migrations land, then flips to `error`.
 *   - `no-native-confirm` — `window.confirm` blocks the event loop, cannot
 *     be styled or tested, and gives destructive actions no pending state.
 *     Use `ConfirmDialog` / `useConfirm`. The imperative `useConfirm()`
 *     binding is resolved by scope analysis, not by name, so it is not
 *     flagged.
 *
 * `no-native-confirm` is at `error` — that sweep is finished. `no-raw-table`
 * stays at `warn` until the residual backlog is triaged; see the rule config
 * below for why it cannot simply be flipped.
 */
const dataSurfacePlugin = {
  rules: {
    "no-raw-table": {
      meta: {
        type: "problem",
        docs: {
          description:
            "Disallow importing the raw table primitives outside components/data-table.",
        },
        messages: {
          rawTable:
            "Raw `ui/table` import. Use `DataTable` from @/components/data-table — it carries the pagination, empty, loading and error states this surface would otherwise re-derive. See frontend/bootstrap.md 'Data surfaces'.",
        },
        schema: [],
      },
      create(context) {
        return {
          ImportDeclaration(node) {
            if (node.source.value === "@/components/ui/table") {
              context.report({ node, messageId: "rawTable" });
            }
          },
        };
      },
    },
    "no-native-confirm": {
      meta: {
        type: "problem",
        docs: { description: "Disallow window.confirm; use ConfirmDialog." },
        messages: {
          nativeConfirm:
            "Native confirm() blocks the event loop and cannot show a pending state. Use `ConfirmDialog` (or `useConfirm()`) from @/components.",
        },
        schema: [],
      },
      create(context) {
        const sourceCode = context.sourceCode ?? context.getSourceCode();
        // A bare `confirm(...)` is only the native dialog when nothing in
        // scope shadows it. `const confirm = useConfirm()` is the sanctioned
        // imperative wrapper and resolves to a local binding, so scope
        // analysis — not the name — decides.
        const isNativeBareCall = (node) => {
          if (node.callee.type !== "Identifier" || node.callee.name !== "confirm") return false;
          let scope = sourceCode.getScope(node);
          while (scope) {
            if (scope.variables.some((v) => v.name === "confirm" && v.defs.length > 0)) {
              return false;
            }
            scope = scope.upper;
          }
          return true;
        };
        const isWindowCall = (node) =>
          node.callee.type === "MemberExpression" &&
          node.callee.object?.type === "Identifier" &&
          node.callee.object.name === "window" &&
          node.callee.property?.name === "confirm";
        return {
          CallExpression(node) {
            if (isWindowCall(node) || isNativeBareCall(node)) {
              context.report({ node, messageId: "nativeConfirm" });
            }
          },
        };
      },
    },
  },
};

const designTokensPlugin = {
  rules: {
    "no-raw-design-values": {
      meta: {
        type: "problem",
        docs: {
          description:
            "Disallow raw hex/rgb colours and arbitrary text/rounded sizes in class strings; use design tokens.",
        },
        messages: {
          hex: "Raw hex colour in a class string. Use a semantic token (text-success, bg-warning, border-danger, bg-info/10 …) — see frontend/bootstrap.md.",
          colorFn:
            "Raw rgb()/hsl() colour in a class string. Use a semantic token — see frontend/bootstrap.md.",
          textSize:
            "Arbitrary text-[Npx] size. Use the type scale (text-2xs … text-2xl) — see frontend/bootstrap.md.",
          roundSize:
            "Arbitrary rounded-[Npx] radius. Use a radius token (rounded-md … rounded-2xl) — see frontend/bootstrap.md.",
        },
        schema: [],
      },
      create(context) {
        const sourceCode = context.sourceCode ?? context.getSourceCode();
        const check = (node, text) => {
          if (HEX.test(text)) context.report({ node, messageId: "hex" });
          if (COLOR_FN.test(text)) context.report({ node, messageId: "colorFn" });
          if (TEXT_SIZE.test(text)) context.report({ node, messageId: "textSize" });
          if (ROUND_SIZE.test(text)) context.report({ node, messageId: "roundSize" });
        };
        return {
          JSXAttribute(node) {
            if (node.name?.name !== "className" || !node.value) return;
            check(node, sourceCode.getText(node.value));
          },
          CallExpression(node) {
            if (node.callee?.type === "Identifier" && VARIANT_HELPERS.has(node.callee.name)) {
              check(node, sourceCode.getText(node));
            }
          },
        };
      },
    },
  },
};

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  prettierConfig,
  {
    rules: {
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" },
      ],
      // The React 19 hooks plugin's strict rules surface a lot of
      // pre-existing patterns. Downgrading to warnings so CI stays
      // green while we burn the queue down on a rolling basis.
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/exhaustive-deps": "warn",
      "react-hooks/error-boundaries": "warn",
      "react-hooks/immutability": "warn",
      "react-hooks/incompatible-library": "warn",
      "react-hooks/static-components": "warn",
      "react-hooks/use-memo": "warn",
      "@next/next/no-img-element": "warn",
    },
  },
  // Design-token guardrail — scoped to the app + component surfaces where
  // Tailwind classes live. Error-level: the #A4 debt is swept, new raw
  // values fail the build.
  {
    files: ["components/**/*.{ts,tsx}", "app/**/*.{ts,tsx}"],
    plugins: { astrolift: designTokensPlugin },
    rules: { "astrolift/no-raw-design-values": "error" },
  },
  // Data-surface guardrail — see the rule's doc block above. Ships at
  // `warn` while the wave migrations land, then flips to `error`.
  {
    files: ["components/**/*.{ts,tsx}", "app/**/*.{ts,tsx}"],
    ignores: ["components/data-table/**"],
    plugins: { astroliftData: dataSurfacePlugin },
    rules: {
      // Still `warn`: the residual backlog is not all list surfaces. A chunk
      // of it is detail / key-value tables where DataTable is the wrong tool
      // and `DefinitionList` is the right home, so flipping this to `error`
      // before that population is triaged would force those through a
      // pagination component that makes no sense for them.
      "astroliftData/no-raw-table": "warn",
      // `error`: the sweep is done, every call site is a ConfirmDialog, and
      // there is no legitimate reason to add a new one.
      "astroliftData/no-native-confirm": "error",
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // GraphQL codegen output — regenerated by `make codegen`.
    "graphql/__generated__/**",
  ]),
]);

export default eslintConfig;
