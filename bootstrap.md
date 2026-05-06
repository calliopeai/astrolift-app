# Astrolift UI -- Bootstrap

Web dashboard for the Astrolift platform. Provides the developer-facing UI for
managing apps, deployments, environments, managed services, teams, and org
settings.

## Stack

| Concern         | Choice                                               |
|-----------------|------------------------------------------------------|
| Framework       | Next.js 16+ (App Router)                             |
| Language        | TypeScript (strict)                                  |
| Data fetching   | Apollo Client 4 + `@apollo/client-integration-nextjs`|
| Styling         | Tailwind 4 + shadcn/ui + Base UI                     |
| Forms           | react-hook-form + zod                                |
| Tables          | TanStack React Table                                 |
| i18n            | next-intl                                            |
| Theming         | next-themes (light / dark / system)                  |
| Icons           | lucide-react                                         |
| Toasts          | Sonner                                               |
| Error tracking  | Sentry                                               |

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
  data-table/     DataTable + DataTableServer + toolbar/pagination
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

## Environment variables

All external URLs and secrets are configured via environment variables. See
`.env.example` for the full list. Key variables:

| Variable                        | Purpose                              |
|---------------------------------|--------------------------------------|
| `NEXT_PUBLIC_API_ROOT`          | Astrolift API base URL               |
| `NEXT_PUBLIC_GRAPHQL_ENDPOINT`  | GraphQL endpoint URL                 |
| `NEXT_PUBLIC_MAIN_PAGE`         | Default redirect after login         |
| `APP_BASE_URL`                  | Public URL of this app               |
| `AUTH0_DOMAIN`                  | Auth0 tenant domain                  |
| `AUTH0_CLIENT_ID`               | Auth0 application client ID          |
| `AUTH0_CLIENT_SECRET`           | Auth0 application client secret      |
| `AUTH0_SECRET`                  | Auth0 session encryption secret      |
| `NEXT_PUBLIC_SENTRY_DSN`       | Sentry DSN (optional)                |
