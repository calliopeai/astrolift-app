# Astrolift UI -- Bootstrap

Web dashboard for the Astrolift platform. Provides the developer-facing UI for
managing apps, deployments, environments, managed services, teams, and org
settings.

## Stack

| Concern        | Choice                                                |
| -------------- | ----------------------------------------------------- |
| Framework      | Next.js 16+ (App Router)                              |
| Language       | TypeScript (strict)                                   |
| Data fetching  | Apollo Client 4 + `@apollo/client-integration-nextjs` |
| Styling        | Tailwind 4 + shadcn/ui + Base UI                      |
| Forms          | react-hook-form + zod                                 |
| Tables         | `components/data-table` (see "Data surfaces")         |
| i18n           | next-intl                                             |
| Theming        | next-themes (light / dark / system)                   |
| Icons          | lucide-react                                          |
| Toasts         | Sonner                                                |
| Error tracking | Sentry                                                |

## GraphQL schema

This UI consumes the GraphQL schema served by `astrolift-api`. When codegen is
configured, types are generated from that schema into `__generated__/graphql.ts`.
Until then, manual types live in `graphql/<domain>/<domain>.types.ts`.

## Local development

```bash
# Install dependencies
npm install

# Copy env template and fill in values
cp .env.example .env

# Start dev server (port 3000)
npm run dev
```

The API must be running at the URL set in `NEXT_PUBLIC_API_ROOT` (defaults to
`http://localhost:8000/`). The GraphQL endpoint is at
`NEXT_PUBLIC_GRAPHQL_ENDPOINT` (defaults to `http://localhost:8000/graphql/`).

## Key directories

```
app/              Next.js App Router pages and layouts
  (app)/          Authenticated pages (sidebar layout)
  (login)/        Public auth pages (centered layout)
  api/            API route handlers (session, token)
  actions/        Server actions
components/       Shared React components
  ui/             shadcn primitives (button, input, card, etc.)
  data-table/     DataTable + useCursorTable + useRowSelection
  forms/          Form builder components
  workflows/      Workflow builder components
graphql/          GraphQL operations, grouped by domain
  <domain>/
    <domain>.queries.ts     Query documents
    <domain>.mutations.ts   Mutation documents
    <domain>.hooks.ts       React hooks wrapping useQuery/useMutation
    <domain>.types.ts       TypeScript types (manual, replaced by codegen later)
    <domain>.fragments.ts   Shared fragments
hooks/            Utility hooks (useDebounce, useLocalStorage, etc.)
lib/              Shared utilities
  apollo/         Apollo Client setup (server client, client provider, cache)
  auth/           Auth0 + token exchange (fetch-token, token-store)
i18n/             Internationalization config
messages/         Locale message files (en.json, es.json, etc.)
public/           Static assets
```

## Conventions

- **Code formatting**: Prettier owns all formatting. Do not hand-tune
  whitespace, quotes, trailing commas, or line breaks. Run `npm run format` to
  fix, `npm run format:check` to verify.
- **Components**: PascalCase filenames (`AppSidebar.tsx`). shadcn components
  live in `components/ui/`.
- **Styling**: Tailwind classes only. No CSS modules, no styled-components.
- **Data fetching**: Apollo Client for all GraphQL. Server components use
  `getClient()`, client components use hooks from
  `graphql/<domain>/<domain>.hooks.ts`.
- **Hooks**: Arrow function exports. `fetchPolicy` must be set explicitly on
  every hook call.
- **Query constants**: SCREAMING_SNAKE_CASE for single queries/mutations
  (`GET_ME`, `UPDATE_PROFILE`). camelCase for paginated connection queries
  (`getApps`, `getDeployments`).
- **Forms**: react-hook-form + zod. Schema in co-located `schema.ts`. Always
  Client Components.
- **Route labels**: Add entries in `lib/routes.ts` for breadcrumb display.
- **Permissions**: PermissionSlug enum in
  `graphql/permissions/permissions.types.ts`. Server guard:
  `requirePermission()`. Client guard: `<PermissionGuard>`.
- **No AI co-authorship messages** in commits or code.

## Data surfaces

Every list and table goes through `components/data-table`. The rules below
are the ones an audit of ~107 surfaces found being broken (epic #1230); the
lint rule `astroliftData/no-raw-table` enforces the first one, because the
previous version of this section was documentation alone and had zero
consumers three months after it was written.

1. **Never import `@/components/ui/table` outside `components/data-table`.**
   Those primitives are DataTable's private dependency. A surface reaching
   for them is about to re-derive pagination, empty, loading and error
   behaviour the component already gets right.
2. **Pagination is server-side, always.** `useCursorTable` is the only data
   mode. Fetching a capped page and paginating it in the browser is what
   made deployment 101 unreachable from `/deployments`. The one sanctioned
   exception is a small fixed-cardinality list (the ~17 system roles) that
   may render unpaginated — still through `DataTable`, for the chrome — and
   the exception must be argued in the PR, not assumed.
3. **The query must expose a `search` argument before the table shows a
   search box.** `searchVariable` opts in; omit it and no box renders.
   Filtering the one page in hand looks like search and is not.
4. **Search is debounced by the controller.** Never hand-roll a
   `setTimeout`, and never wire an input straight into query variables —
   `/administration/audit` issued one network round trip per keystroke.
5. **`empty` and `emptyFiltered` are different states.** "You have no
   clusters yet" and "no cluster matches 'prod'" want different words and
   different actions. `empty` is a required prop; the type will not let you
   render nothing.
6. **Loading renders skeleton rows inside the real table**, sized from the
   column defs. DataTable does this for you — do not swap the table out for
   a floating `<Skeleton>` block, which reflows the page when rows land.
7. **Sorting is server-side, declared per column** via `Column.sortKey`
   plus the controller's `sortVariable`. There is no client sort: reordering
   one page while the rest of the result set sits on the server produces an
   order that is wrong at every page boundary.
8. **`rowHref` XOR `onRowActivate`.** Rows that navigate must be real links
   so middle-click, open-in-new-tab and copy-link work. Rows that do
   something else must not pretend to be links. The types enforce the choice.
9. **Selection comes from `useRowSelection`.** It persists across pages, so
   the count in the bulk bar means what it says.
10. **Destructive actions confirm** via `ConfirmDialog` / `useConfirm`.
    `window.confirm` is lint-flagged: it blocks the event loop and gives the
    action no pending state.

Typical shape:

```tsx
const table = useCursorTable<Deployment>({
  query: LIST_DEPLOYMENTS_PAGE,
  extract: (d) => (d as Resp | undefined)?.astroliftDeploymentsPage,
  searchVariable: "search",
  urlKey: "dep",
});

<DataTable
  label="Deployments"
  controller={table}
  columns={columns}
  getRowId={(d) => d.id}
  rowHref={(d) => `/deployments/${d.id}`}
  empty={{ icon: <RocketIcon />, title: "No deployments yet", ... }}
/>;
```

### The allowlist

`no-raw-table` runs at **error**. The surfaces that still import the
primitives are named, one per line with the blocker that keeps them there,
in `eslint/raw-table-allowlist.mjs` — matched by exact path, because
minimatch reads the `[slug]` in a route directory as a character class.

`eslint/raw-table-allowlist.test.ts` asserts the list is exactly the set of
files importing `@/components/ui/table`. Migrating a surface therefore
includes deleting its entry, or the test fails; adding a raw table fails
lint until an entry is added in review. The list may only shrink.

## Design system

The theme lives in `app/globals.css`. Brand seeds (`--brand-*`, the teal +
navy shared with `astrolift-site` / `astrolift-docs`) are **never** edited
here; the app adds a _semantic_ + _scale_ layer on top. All tokens resolve in
both light and dark; a guardrail keeps raw values from creeping back in.

### Status tokens

Four states, each a solid `base` plus `-fg` / `-bg` / `-border`:

| Token         | Use                                 |
| ------------- | ----------------------------------- |
| `--success` … | positive / healthy / running        |
| `--warning` … | degraded / attention                |
| `--info` …    | neutral informational / in-progress |
| `--danger` …  | failed / destructive                |

| Variant   | Utility example          | Role                                                               |
| --------- | ------------------------ | ------------------------------------------------------------------ |
| base      | `text-success` `bg-info` | solid accent: dots, icons, chart series, emphasis borders          |
| `-fg`     | `text-warning-fg`        | **accessible text/icon** on the matching tint (AA on card + `-bg`) |
| `-bg`     | `bg-danger-bg`           | subtle tinted fill for callouts / badges                           |
| `-border` | `border-info-border`     | hairline for a tinted container                                    |
| alpha     | `bg-success/10`          | ad-hoc tint straight off the base                                  |

Rule of thumb for a status callout: `bg-{s}-bg text-{s}-fg border-{s}-border`.
For a status dot or chart series, use the bare `{s}` base. Prefer `-fg` for
text (the bare base is tuned for accents, not body copy). These replace the
hardcoded `emerald/amber/indigo/red` classes (swept in #A4).

### Type scale

Named steps only. `text-2xs` (11px) is the micro step that retires the
`text-[10px]` / `text-[11px]` escapes; `text-xs` → `text-2xl` are the Tailwind
defaults (unchanged). Never reach for `text-[Npx]`.

### Surface / elevation

`bg-surface-0` (page) · `bg-surface-1` (card) · `bg-surface-2` (raised /
popover). Express depth by **surface**, not by stacking borders. In dark mode
these are the three navy levels; in light they pair with a shadow.

### Motion

`--motion-fast` (120ms) · `--motion-base` (200ms) · `--motion-slow` (320ms),
consumed via `var(--motion-base)` (e.g. `duration-[var(--motion-base)]`), plus
`ease-standard` / `ease-emphasized` utilities. All heavy "mission-control"
motion sits behind `@media (prefers-reduced-motion: reduce)`, which is reset
globally in the base layer.

### The container rule (de-nesting)

**`PageShell > Section > (Card | Table | Chart)` — max 2 container depths.**
A `Card` is a bounded _object_; a page _region_ is a `Section` (a heading, not
a border). Never nest a `Section` inside a `Card`, and never wrap a bordered
box around another bordered box.

- Do: `<Section title="Deployments"><Card>…</Card></Section>`
- Don't: `<section className="rounded-lg border p-5"><Card>…</Card></section>`
- Do: metadata via `<DefinitionList>` inside a Card.
- Don't: a grid of mini-cards for key/value metadata.

### Primitives (`components/ui/`)

- **`Section`** — a page region: heading + optional description/action, no
  border by default (spacing, or a hairline via `divided`). Replaces the
  `section.rounded-lg border` idiom.
- **`StatTile`** — the canonical dense metric card (label + value + optional
  icon / trend / `sparkline` slot). Supersedes `KpiTile`, which is now a thin
  adapter over it; new stat surfaces import `StatTile` directly.
- **`DefinitionList`** — key/value metadata as a semantic `<dl>` (`row` or
  `stack`). Replaces metadata mini-cards.

### Guardrail

`astrolift/no-raw-design-values` (ESLint, **error**) flags, in `className` /
`cva` / `tv` under `components/**` and `app/**`: raw hex, `rgb()`/`hsl()`,
arbitrary `text-[Npx]`, and arbitrary `rounded-[Npx]`. Token-referencing
arbitrary values (`bg-[var(--x)]`, `rounded-[calc(…)]`) are allowed. The debt
surfaced at warn-level was swept in #A4 / #1050; new raw values now fail the
build. Escape hatch for unavoidable chart / SVG / terminal exact
colours:

```tsx
// eslint-disable-next-line astrolift/no-raw-design-values
className = "… bg-[#0b0f17]";
```

## Environment variables

All external URLs and secrets are configured via environment variables. See
`.env.example` for the full list. Key variables:

| Variable                       | Purpose                         |
| ------------------------------ | ------------------------------- |
| `NEXT_PUBLIC_API_ROOT`         | Astrolift API base URL          |
| `NEXT_PUBLIC_GRAPHQL_ENDPOINT` | GraphQL endpoint URL            |
| `NEXT_PUBLIC_MAIN_PAGE`        | Default redirect after login    |
| `APP_BASE_URL`                 | Public URL of this app          |
| `AUTH0_DOMAIN`                 | Auth0 tenant domain             |
| `AUTH0_CLIENT_ID`              | Auth0 application client ID     |
| `AUTH0_CLIENT_SECRET`          | Auth0 application client secret |
| `AUTH0_SECRET`                 | Auth0 session encryption secret |
| `NEXT_PUBLIC_SENTRY_DSN`       | Sentry DSN (optional)           |
