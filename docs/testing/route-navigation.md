# Production route navigation

`frontend/e2e-routes/reachability.spec.ts` reads the generated `ROUTES.md`
inventory. The owner test starts at `/dashboard` and follows links rendered
by the production application, including account menus, breadcrumb switchers,
table overflow menus, section links and the event list's aggregation control.
Each active page must occur in that rendered navigation graph. Adding a route
to the dictionary without a reachable link fails the test. The graph is saved
as `route-graph.json` in Playwright's test artifacts.

Compatibility aliases are checked separately for real redirects to declared
active routes. Parked routes must produce the production Next not-found
boundary; streamed client not-found responses may have HTTP status 200.
Anonymous protected routes must redirect to login. Reader and member fixtures
exercise actual navigation and restricted creation affordances. Cold-load
checks cover app pod preloads and workflow run context resolution.

The HTTP fixture server executes each operation against the committed GraphQL
SDL. Its owner, reader and member identities provide controlled module and
permission responses. These tests verify frontend behavior, not backend role
authorization. Backend PostgreSQL RoleBinding, HTTP, encrypted storage and
runtime dispatch tests provide the separate pipeline-secret authorization
evidence. The fixture server rejects mutations and the route walk requires
zero mutations and zero GraphQL contract errors. No deployment API is used.

Run locally from `frontend`:

```sh
NEXT_PUBLIC_API_ORIGIN=http://127.0.0.1:6172 API_INTERNAL_ROOT=http://127.0.0.1:6172 TZ=UTC npm run build
TZ=UTC npm run test:routes
```

Use `ROUTE_PORT` and `ROUTE_API_PORT` for independent concurrent runs. Build
with that API port as well: Next embeds the rewrite origin during compilation.
CI runs this separate route config after the production build. Routes write
artifacts to `test-results/routes`, and the Storybook layout suite writes to
`test-results/layout`, so one suite's cleanup cannot delete the other's traces.
The suites retain separate config files and catalog/application servers.

The complete owner graph has a fifteen-minute aggregate budget, with individual
navigation still capped at twenty seconds. Its trace retains calls, network
activity and source locations; a failed page also gets a screenshot and error
context. Recording DOM snapshots at every disclosure in the whole graph produced
nearly fourteen thousand snapshots and a 353 MiB trace in CI, so this long walk
uses the rendered graph and failure context instead. Assertions, visited routes,
roles, aliases and mutation/contract checks are unchanged.
