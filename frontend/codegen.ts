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
    // Pipelines queries/mutations reference schema fields that don't exist yet
    // (slug, description, secrets on AstroliftPipeline; SetPipelineSecretInput).
    // Excluded until the backend schema catches up.
    "!graphql/pipelines/pipelines.queries.ts",
    "!graphql/pipelines/pipelines.mutations.ts",
    // These files interpolate PLAIN template-literal constants (bare field
    // lists, not gql-tagged fragments) into their gql documents, e.g.
    // `${AGENT_LIST_ITEM_FIELDS}`. graphql-tag-pluck can't resolve those,
    // substitutes nothing, and the resulting document fails to parse — which
    // (before `noSilentErrors` below) silently dropped EVERY operation in the
    // file from operations.ts (#934). Excluded so codegen stays green; with
    // `noSilentErrors: true` any new offender now fails the run loudly instead
    // of vanishing. To re-include a file, rewrite its shared field-list
    // constants as named gql fragments and spread them (`...FragmentName`).
    "!graphql/agents/agents.queries.ts",
    // identity.queries.ts was re-included in #1232: its last plain constant
    // (IDP_FIELDS) is a named fragment now, so the eight cursor-page
    // documents the members/teams/projects/policies/tokens tables walk are
    // typed rather than hand-written at each call site.
    "!graphql/identity/identity.mutations.ts",
    "!graphql/lifecycle/lifecycle.mutations.ts",
    "!graphql/observability/observability.queries.ts",
    "!graphql/operations/alerts.queries.ts",
    "!graphql/registry/registry.queries.ts",
    "!graphql/registry/registry.mutations.ts",
    "!graphql/scm/scm.queries.ts",
    "!graphql/services/services.mutations.ts",
    "!graphql/workflows/tiered.queries.ts",
    "!graphql/workflows/tiered.mutations.ts",
  ],
  ignoreNoDocuments: true,
  // Shared plugin config. The CLI also spreads this object into
  // @graphql-tools loadDocuments options, which is how `noSilentErrors`
  // reaches the code-file loader: without it, a gql template that
  // graphql-tag-pluck can't extract (see the exclusion block above) makes the
  // loader swallow the parse error and return zero documents for the whole
  // file — operations disappear from operations.ts with a green exit code.
  // With it, the run fails and names the offending file.
  config: {
    noSilentErrors: true,
  },
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
