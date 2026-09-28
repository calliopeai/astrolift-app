import path from "node:path";

import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import prettierConfig from "eslint-config-prettier";

import { RAW_TABLE_ALLOWLIST_FILES } from "./eslint/raw-table-allowlist.mjs";

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
// Tailwind's own palette (bg-emerald-500, text-gray-400): status and chrome
// colours come from the semantic tokens so a theme or accent change reaches
// them. Checked in every string, since class maps live in plain objects too.
const RAW_PALETTE =
  /(?<![\w-])(?:[a-z-]+:)*(?:bg|text|border|ring|fill|stroke|from|to|via|outline|divide|shadow|decoration|accent|caret)-(?:red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|slate|gray|zinc|neutral|stone)-\d{2,3}\b/;
// Arbitrary padding, margin and gap in px or rem: use the spacing scale.
// Positioning (inset, top, left) is geometry, not rhythm, and stays allowed.
const ARBITRARY_SPACING =
  /(?<![\w-])-?(?:p|px|py|pt|pb|pl|pr|ps|pe|m|mx|my|mt|mb|ml|mr|ms|me|gap|gap-x|gap-y|space-x|space-y)-\[[0-9.]+(?:px|rem)\]/;

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
 *     behaviour that the component already gets right.
 *   - `no-native-confirm` — `window.confirm` blocks the event loop, cannot
 *     be styled or tested, and gives destructive actions no pending state.
 *     Use `ConfirmDialog` / `useConfirm`. The imperative `useConfirm()`
 *     binding is resolved by scope analysis, not by name, so it is not
 *     flagged.
 *
 * Both are at `error`. `no-raw-table` carries an explicit allowlist
 * (`eslint/raw-table-allowlist.mjs`) naming every file that still imports
 * the primitives and why; it is matched by exact path, not by glob, and a
 * test asserts it stays exactly in step with the tree.
 */
const dataSurfacePlugin = {
  rules: {
    "no-raw-table": {
      meta: {
        type: "problem",
        docs: {
          description: "Disallow importing the raw table primitives outside components/data-table.",
        },
        messages: {
          rawTable:
            "Raw `ui/table` import. Use `DataTable` from @/components/data-table — it carries the pagination, empty, loading and error states this surface would otherwise re-derive. See frontend/bootstrap.md 'Data surfaces'. If this surface genuinely cannot use DataTable, add it to eslint/raw-table-allowlist.mjs with the blocker written out.",
        },
        // The allowlist is passed as exact repo-relative paths rather than
        // matched with a `files` override: several of these live under
        // `app/(app)/apps/[slug]/…`, and minimatch reads `[slug]` as a
        // character class, so a glob override would silently miss them.
        schema: [{ type: "array", items: { type: "string" }, uniqueItems: true }],
      },
      create(context) {
        const allowlist = context.options[0] ?? [];
        const relative = path.relative(context.cwd, context.filename).split(path.sep).join("/");
        const exempt = allowlist.some(
          (entry) => relative === entry || relative.endsWith(`/${entry}`)
        );
        if (exempt) return {};
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
          palette:
            "Raw Tailwind palette colour. Use a semantic token (text-success-fg, bg-warning/10, border-danger-border, text-muted-foreground, bg-muted …) — see frontend/bootstrap.md.",
          spacing:
            "Arbitrary px/rem padding, margin or gap. Use the spacing scale (p-3, gap-1.5 …).",
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
          if (ARBITRARY_SPACING.test(text)) context.report({ node, messageId: "spacing" });
        };
        const checkPalette = (node, text) => {
          if (RAW_PALETTE.test(text)) context.report({ node, messageId: "palette" });
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
          Literal(node) {
            if (typeof node.value === "string") checkPalette(node, node.value);
          },
          TemplateElement(node) {
            checkPalette(node, node.value.raw);
          },
        };
      },
    },
  },
};

/**
 * Storybook-first guardrail (spec 44 §8; the rule in frontend/bootstrap.md).
 *
 * Every component and every screen is built in the catalog first; a route
 * under `app/` only fetches data and renders a screen from `components/`.
 * So `app/` holds no markup of its own:
 *
 *   - `no-markup-in-app` — no intrinsic JSX (`<div>`, `<span>`, `<table>`…).
 *     The document shell (`html`, `head`, `body`) is the one exception; the
 *     root layout has to render it.
 *   - `no-ui-in-app` — no `@/components/ui/*` imports. Primitives compose
 *     into screens in `components/`, where they have stories; a route that
 *     reaches for them is building UI outside the catalog.
 *
 * Both at `error`, no allowlist: Leo chose "hard now".
 */
const DOCUMENT_TAGS = new Set(["html", "head", "body"]);

const screensPlugin = {
  rules: {
    "no-markup-in-app": {
      meta: {
        type: "problem",
        messages: {
          markup:
            "<{{tag}}> in app/: build the markup as a screen in components/ with a story, and render that screen here.",
        },
        schema: [],
      },
      create(context) {
        return {
          JSXOpeningElement(node) {
            if (node.name.type !== "JSXIdentifier") return;
            const tag = node.name.name;
            if (!/^[a-z]/.test(tag) || DOCUMENT_TAGS.has(tag)) return;
            context.report({ node, messageId: "markup", data: { tag } });
          },
        };
      },
    },
    "no-ui-in-app": {
      meta: {
        type: "problem",
        messages: {
          ui: "{{source}} in app/: primitives compose into screens in components/, not in routes.",
        },
        schema: [],
      },
      create(context) {
        return {
          ImportDeclaration(node) {
            const source = node.source.value;
            if (typeof source === "string" && source.startsWith("@/components/ui/")) {
              context.report({ node, messageId: "ui", data: { source } });
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
  // Data-surface guardrail — see the rule's doc block above.
  {
    files: ["components/**/*.{ts,tsx}", "app/**/*.{ts,tsx}"],
    // DataTable is the one surface built on the primitives; the table's own
    // story is its catalog entry (Storybook first), not a data surface.
    ignores: ["components/data-table/**", "components/ui/table.stories.tsx"],
    plugins: { astroliftData: dataSurfacePlugin },
    rules: {
      // `error`: at `warn` the count went 59 → 38 → 35 across three waves
      // while new surfaces kept shipping past it. The residue is enumerated
      // in eslint/raw-table-allowlist.mjs, one entry per file with its
      // blocker; that list may only shrink, and a test holds it in step
      // with the tree.
      "astroliftData/no-raw-table": ["error", RAW_TABLE_ALLOWLIST_FILES],
      // `error`: the sweep is done, every call site is a ConfirmDialog, and
      // there is no legitimate reason to add a new one.
      "astroliftData/no-native-confirm": "error",
    },
  },
  // Radix is wrapped once, in components/ui; everything else composes those
  // primitives so focus, motion and theming stay in one place.
  {
    files: ["components/**/*.{ts,tsx}", "app/**/*.{ts,tsx}"],
    ignores: ["components/ui/**"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@radix-ui/*", "radix-ui", "radix-ui/*"],
              message:
                "Import the wrapped primitive from @/components/ui instead of Radix directly.",
            },
          ],
        },
      ],
    },
  },
  // Storybook-first guardrail — see the rule's doc block above.
  {
    files: ["app/**/*.tsx"],
    ignores: ["app/**/*.test.tsx"],
    plugins: { astroliftScreens: screensPlugin },
    rules: {
      "astroliftScreens/no-markup-in-app": "error",
      "astroliftScreens/no-ui-in-app": "error",
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
    // The component catalog build — `npm run build-storybook`.
    "storybook-static/**",
  ]),
]);

export default eslintConfig;
