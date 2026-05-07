# astrolift-app — Bootstrap

> **What this file is.** Repo-local conventions for `astrolift-app`. Read the workspace `bootstrap.md` (in the metarepo) first for cross-repo context.

## Purpose

This repo is the Astrolift control plane: backend (Django + Strawberry GraphQL + Temporal) and frontend (Next.js + React 19 + Apollo + Tailwind) living together so a single PR can ship a feature end-to-end.

The split:

```
backend/      Django + Strawberry GraphQL + Temporal — the control plane
frontend/     Next.js + React 19 + Apollo + Tailwind — the operator UI
docker/       docker-compose + entrypoint + runtime config (gitignored)
schema.graphql  GraphQL contract — backend writes it, frontend codegens against it
```

For platform architecture and infra context, see the metarepo `specs/` directory.

## Quick start

```bash
./bootstrap.sh        # one-time setup (env files, deps check)
./run.sh up           # start backend + UI + supporting services

./run.sh seed         # re-seed dev identity (acme/eng/api/dev@local.astrolift.net)
./run.sh shell        # bash in the backend container
./run.sh logs         # tail backend logs
./run.sh urls         # print local URLs
```

URLs:

- Frontend       — http://localhost:3000
- Backend `/app/` — http://localhost:8000/app/
- GraphQL        — http://localhost:8000/app/gql/config/
- Admin          — http://localhost:8000/app/admin/
- Temporal UI    — http://localhost:8233
- Mailpit        — http://localhost:8025

## Conventions

Inherit from the workspace `bootstrap.md`. Repo-specific:

- **Schema is the contract.** When backend lands a new GraphQL field, run `make schema` to write `schema.graphql` at the repo root, then `cd frontend && npm run codegen` (or whatever the codegen command resolves to). Atomic schema-changing PRs land both halves at once.
- **Tests run inside the compose stack.** `./run.sh test` runs `pytest` inside `astrolift-local` against real Postgres + Temporal. Don't mock the DB — workspace rule.
- **Comments explain WHY only.** Default to no comments. The code says what it does.
- **No co-authorship trailers in commits. No rebases.** New commits only.
- **MutationResult { ok, errors, data? }** envelope on every mutation; resolver-entry permission checks; deny-by-default.
- **Soft delete on every business model.** Set `deleted_at`/`deleted_by`, never call `.delete()`.

## What's already in place

| Layer | What's there |
|---|---|
| Auth | auth1 module (session-based) with dev-login bypass for local |
| Data | Postgres 17, `Tracking` base model (created/updated/deleted by+at, version, soft deletes) |
| API | GraphQL (Strawberry), DRF for CLI-facing REST, file upload via S3/MinIO |
| Permissions | Permission catalog, `@require_permission` decorator, RoleBinding-based resolver |
| Async | Temporal worker fleet — workflows for onboarding, deploy, rollback |
| Search | OpenSearch (gated behind the `search` compose profile) |
| Email | django-anymail / Mailpit (local) |
| Observability | Structured JSON logging, request-ID middleware, OTel trace correlation |
| Frontend | Next.js 15 + React 19 + Apollo + Tailwind v4 + shadcn |
| Brand | Astrolift teal/navy palette shared with astrolift.ai + astrolift.dev |
| Local stack | docker-compose: api, ui, postgres, redis, temporal, mailpit, minio (profile), opensearch (profile) |
