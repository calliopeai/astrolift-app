# AGENTS.md — Astrolift App

Generic shim for AI coding agents (covers tools that don't have their
own `<TOOL>.md` shim). All shims point to **[`bootstrap.md`](./bootstrap.md)**
as the single source of truth.

Read `bootstrap.md` first; then **[`backend/bootstrap.md`](./backend/bootstrap.md)**
and **[`frontend/bootstrap.md`](./frontend/bootstrap.md)** for layer-specific
conventions.

## Top-of-mind rules

1. **No rebases.** New commits only.
2. **No AI / co-author attribution** in commits.
3. **Push submodules before parent metarepo.**
4. **Soft delete on every business model** (`deleted_at` / `deleted_by`).
5. **`MutationResult { ok, errors, data? }`** envelope on every mutation.
6. **Permission check first line of every resolver / mutation.** Deny-by-default.
7. **Tests against real Postgres + real Temporal**, not mocks.
8. **Schema is the contract.** Backend change → `make schema` → `make codegen`.
9. **No N+1 queries.** Prefetch / dataloader fan-out resolvers.
10. **Storybook first.** No component reaches the app unless it is in
   Storybook; screens are pure and built there against typed fixtures, and
   `app/` routes only fetch data and render a screen. Enforced by a story-per-
   component test and the `no-markup-in-app` lint rule. See
   `frontend/bootstrap.md` › Storybook first.

## Pre-commit checklist

- `cd backend && ruff format --check . && ruff check . && mypy core config`
- `docker compose exec ui npm run lint`
- `docker compose exec ui npx tsc --noEmit`
- `./run.sh test`

Full conventions, git workflow, and architecture in `bootstrap.md`.
