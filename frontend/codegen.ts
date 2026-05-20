/**
 * GraphQL Code Generator config.
 *
 * Generates TypeScript types from `backend/schema.graphql` (the
 * authoritative source — Strawberry exports the live schema there
 * via `make schema-dump` / a CI step). Output lives in
 * `graphql/__generated__/` and is committed so contributors don't
 * need a local backend to typecheck.
 *
 * Two outputs:
 *  - `schema.ts`: every type defined in the schema, with named
 *    exports matching the GraphQL type name (e.g.
 *    `AstroliftDeployment`). Replaces hand-written entries in the
 *    domain `*.types.ts` files.
 *  - `operations.ts`: typed shapes for every query/mutation/
 *    subscription in `**\/*.queries.ts` + `**\/*.mutations.ts`,
 *    keyed by the operation name (e.g. `ListDeploymentsQuery`,
 *    `ListDeploymentsQueryVariables`). Replaces the hand-rolled
 *    `Resp` interfaces in client components.
 *
 * Run:
 *   npm run codegen
 *   npm run codegen:watch    # rebuild as you edit
 */

import type { CodegenConfig } from "@graphql-codegen/cli";

const config: CodegenConfig = {
  schema: "./schema.graphql",
  documents: [
    "graphql/**/*.queries.ts",
    "graphql/**/*.mutations.ts",
    "graphql/**/*.subscriptions.ts",
    // Legacy queries that reference fields not on the Astrolift
    // schema (predecessor-template `me`, `formSubmissions`, the
    // legacy permissions `component`, etc.) are excluded from
    // operation typing — codegen refuses the whole document set
    // otherwise. They keep their hand-rolled types until they're
    // rewritten to Astrolift-shaped queries.
    "!graphql/user/user.queries.ts",
    "!graphql/forms/forms.queries.ts",
    "!graphql/forms/forms.mutations.ts",
    "!graphql/permissions/permissions.queries.ts",
    "!graphql/employees/employees.queries.ts",
  ],
  ignoreNoDocuments: true,
  generates: {
    "graphql/__generated__/schema.ts": {
      plugins: ["typescript"],
      config: {
        // GUID is a custom scalar in the Astrolift schema (UUID v7
        // string at the wire level); map it to a string alias so
        // imports stay clean.
        scalars: {
          GUID: "string",
          DateTime: "string",
          JSON: "Record<string, unknown>",
        },
        // Generated unions stay readable; numeric enums are confusing
        // in TS, prefer string literals.
        enumsAsTypes: true,
        // Don't emit __typename on every type — clutter at this layer
        // is real and Apollo adds it on the wire when it needs it.
        skipTypename: true,
        avoidOptionals: { field: false, inputValue: true },
        immutableTypes: false,
      },
    },
    "graphql/__generated__/operations.ts": {
      // Self-contained operations file: chain `typescript` +
      // `typescript-operations` so the operations file emits its own
      // helper types (Exact, Maybe, etc.) + input types + enums.
      // We tried (and rejected): (a) `importTypesFrom` config —
      // silently ignored by the bare plugin; (b) `import-types`
      // preset — breaks on graphql-codegen 5 with 'visitFn.call is
      // not a function'; (c) `add` plugin to import helpers — works
      // for helpers but not for the dozens of input/enum types the
      // operations reference. Inlining is the only path that
      // type-checks reliably.
      plugins: ["typescript", "typescript-operations"],
      config: {
        scalars: {
          GUID: "string",
          DateTime: "string",
          JSON: "Record<string, unknown>",
        },
        skipTypename: true,
        avoidOptionals: { field: false, inputValue: true },
        enumsAsTypes: true,
      },
    },
  },
};

export default config;
