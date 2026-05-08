# Generated GraphQL types

`schema.ts` and `operations.ts` are produced by `npm run codegen`
from `frontend/schema.graphql` (which itself is dumped from the
live Strawberry schema by `make schema` at the repo root).

**Don't hand-edit anything in this directory.** The next codegen run
overwrites your changes.

## When to regenerate

- After any change to a Python file under `backend/astrolift_*/schema/`
  (types, queries, mutations).
- After updating `core.permissions.Permission`.
- Before opening a PR that touches GraphQL surfaces — CI will fail
  if the generated types are stale relative to the schema.

```bash
make codegen-all      # dump schema + regenerate types
# or, if you only edited a .queries.ts:
make codegen
```

## Migration path for hand-rolled `*.types.ts`

The repo has hand-rolled type interfaces under `graphql/<domain>/<domain>.types.ts`.
Each domain migrates incrementally:

1. Find the generated equivalent in `__generated__/schema.ts` (the
   GraphQL type name matches: `AstroliftDeployment`, `AstroliftPolicy`, …).
2. In the `*.types.ts` file, re-export from `__generated__`:
   ```ts
   export type { AstroliftDeployment } from "@/graphql/__generated__/schema";
   ```
3. Drop the hand-rolled `interface AstroliftDeployment` declaration.
4. Repeat for each type the file owned.

Type names that aren't on the Astrolift schema (legacy `me`, `formSubmissions`,
`component`, etc.) keep their hand-rolled definitions until those
queries are rewritten to Astrolift-shaped ones — codegen excludes
those query files from operation typing (see `codegen.ts`).
