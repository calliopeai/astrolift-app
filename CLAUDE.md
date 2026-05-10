# CLAUDE.md — Astrolift App

Technical reference for AI agents working in this repo lives in
**[`bootstrap.md`](./bootstrap.md)**. Read it first.

For backend-specific conventions, also read
**[`backend/bootstrap.md`](./backend/bootstrap.md)**;
for frontend-specific conventions, **[`frontend/bootstrap.md`](./frontend/bootstrap.md)**.

## Top-of-mind rules

These also live in `bootstrap.md`; surfacing here so a quick-glance agent
sees them:

1. **No rebases.** New commits only. No `git rebase`, no `--amend`.
2. **No AI / co-author attribution** in commits or PR bodies.
3. **Push submodules before the parent metarepo.** This repo is a
   submodule of `astrolift`.
4. **Soft delete on every business model.** Set `deleted_at` /
   `deleted_by`; never call `.delete()` on a `Tracking` model.
5. **`MutationResult { ok, errors, data? }` envelope on every mutation.**
   Never raise from a resolver or mutation.
6. **Permission check at the top of every resolver / mutation.** First
   line. Deny-by-default. No integer PKs in APIs.
7. **Tests against real Postgres + real Temporal** (test env), not mocks.
   `./run.sh test` runs `pytest -x` inside the compose stack.
8. **Schema is the contract.** Backend GraphQL changes → `make schema` →
   `make codegen`. Don't break the contract without updating both sides.
9. **Avoid N+1 queries.** Use prefetching / dataloaders on GraphQL
   resolvers that fan out across relations.

## Pre-commit checklist

- `cd backend && ruff format --check . && ruff check . && mypy core config`
- `docker compose -f docker/docker-compose.yaml exec ui npm run lint`
- `docker compose -f docker/docker-compose.yaml exec ui npx tsc --noEmit`
- `./run.sh test` (or `make test`)

For everything else, see `bootstrap.md`.
