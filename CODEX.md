# CODEX.md — Astrolift App

Technical reference for AI agents working in this repo lives in
**[`bootstrap.md`](./bootstrap.md)**. Read it first.

For backend-specific conventions, also read
**[`backend/bootstrap.md`](./backend/bootstrap.md)**;
for frontend-specific conventions, **[`frontend/bootstrap.md`](./frontend/bootstrap.md)**.

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

## Pre-commit checklist

- `cd backend && ruff format --check . && ruff check . && mypy core config`
- `docker compose exec ui npm run lint`
- `docker compose exec ui npx tsc --noEmit`
- `./run.sh test`

Full conventions, git workflow, and architecture in `bootstrap.md`.
