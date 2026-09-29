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
10. **Storybook first.** No component reaches the app unless it is in
   Storybook; screens are pure and built there against typed fixtures, and
   `app/` routes only fetch data and render a screen. Enforced by a story-per-
   component test and the `no-markup-in-app` lint rule. See
   `frontend/bootstrap.md` › Storybook first.

## Pre-commit checklist

- `cd backend && ruff format --check . && ruff check . && mypy core config`
- `cd backend/providers && ruff format --check . && ruff check .` — a separate
  run on purpose. `providers/` is its own package and pins `ruff>=0.15` while the
  backend pins `>=0.6`, so the line above does not cover it and the ambient ruff
  cannot even parse `providers/pyproject.toml` (`Unknown rule selector: TC`).
  CI lints it in the `test (providers)` job; skipping it locally means finding
  out there. Install the pinned version with
  `pip install --target <dir> "ruff>=0.15,<0.16"` and run that binary.
- `docker compose -f docker/docker-compose.yaml exec ui npm run lint`
- `docker compose -f docker/docker-compose.yaml exec ui npx tsc --noEmit`
- `./run.sh test` (or `make test`)

For everything else, see `bootstrap.md`.
