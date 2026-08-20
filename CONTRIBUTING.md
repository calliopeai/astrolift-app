# Contributing to Astrolift App

Thanks for your interest in contributing! This is the **Astrolift control plane** — Django + Strawberry GraphQL + Temporal on the backend, Next.js + React 19 + Apollo on the frontend, all in one repo so a single PR can ship a feature end-to-end.

## Getting Started

1. Fork the repository
2. Clone your fork
3. Read [`bootstrap.md`](bootstrap.md) for repo conventions
4. Read [`backend/bootstrap.md`](backend/bootstrap.md) and [`frontend/bootstrap.md`](frontend/bootstrap.md) for layer-specific conventions
5. Run `./bootstrap.sh && ./run.sh up` to bring up the local stack (docker-compose: api, ui, postgres, redis, temporal, mailpit)
6. Create a feature branch from `main`: `feature/<issue>-<short-desc>` or `fix/<issue>-<short-desc>`

## Development Process

1. Pick an issue from the project's issue tracker
2. Comment your plan on the issue before starting
3. Make your changes following the conventions in `bootstrap.md`
4. Run the full quality gate locally before pushing (see [Testing](#testing) below)
5. Open a PR with a clear summary and test plan; reference the issue (`closes #N` or `ref #N`)
6. Iterate on review feedback with **new commits** — no force-pushes, no rebases

## Conventions

These are non-negotiable. They live in `bootstrap.md` (and the backend/frontend equivalents); the highlights:

- **Soft delete on every business model.** Set `deleted_at` / `deleted_by`; never call `.delete()` on anything that inherits `Tracking`.
- **`MutationResult { ok, errors, data? }` envelope on every mutation.** Never raise from a resolver or mutation.
- **Permission check at the top of every resolver and mutation.** First line. Deny-by-default.
- **No integer PKs in APIs.** Use UUID, slug, or content-addressed keys.
- **Validate at boundaries.** All input validated at the GraphQL / DRF entry point.
- **Schema is the contract.** When the backend lands a new GraphQL field: `make schema`, then `make codegen` (or `make codegen-all` for both). Atomic schema-changing PRs land both halves at once.
- **Tests against real Postgres + real Temporal.** Don't mock the DB. Don't mock Temporal. The compose stack provides both.
- **Comments explain WHY, not WHAT.** Default to no comments. The code says what it does.
- **Conventional Commits.** `feat(scope): …`, `fix(scope): …`, `chore(scope): …`, etc.
- **No co-author / AI attribution trailers.** Ever.
- **No rebases.** New commits only. No `--amend`.

## Testing

The full quality gate, run before every PR:

### Backend

```bash
# All run inside the compose stack against real Postgres + Temporal
./run.sh test                        # pytest -x (preferred entry)
make test                            # equivalent

cd backend && ruff format --check .  # formatter check
cd backend && ruff check .           # linter
cd backend && mypy core config       # type check
```

`make fmt` will auto-fix formatting and most lint issues.

### Frontend

```bash
docker compose -f docker/docker-compose.yaml exec ui npm run lint
docker compose -f docker/docker-compose.yaml exec ui npx tsc --noEmit
docker compose -f docker/docker-compose.yaml exec ui npm run codegen   # if schema.graphql changed
```

Or, if you have node installed and `frontend/node_modules` populated:

```bash
cd frontend && npm run lint
cd frontend && npx tsc --noEmit
cd frontend && npm run format:check
cd frontend && npm run codegen
```

## Pull Requests

- One issue per PR. Don't batch unrelated changes.
- Don't refactor surrounding code that wasn't part of the issue.
- Keep PRs reviewable — split large work into stacked PRs if needed.
- The PR description should explain *what* changed and *why*. Reviewers can read the diff for the *how*.
- All checks must pass before merge: tests, lint, typecheck, codegen drift check.

## Issue Reporting

- Search existing issues first
- For bugs: include reproduction steps, expected vs actual, and your environment (`./run.sh ps` output is helpful)
- For features: explain the use case before proposing an implementation
- For security: see [`SECURITY.md`](SECURITY.md) — do not file public issues for vulnerabilities

## License

Astrolift is **MIT-licensed** (see [`LICENSE`](LICENSE)). Contributions are
accepted under the same terms: by opening a pull request you agree your work is
contributed under the MIT license.

**There is no CLA, deliberately.** The MIT grant already covers sublicensing and
sale, so contributed code can be commercialised without collecting signatures
from every past contributor. That is one of the reasons the project is MIT
rather than copyleft.

Two things to know before you write code:

- **Sign off your commits** with `git commit -s`. That adds a
  `Signed-off-by:` line asserting you wrote the patch or otherwise have the
  right to submit it under the MIT license (the
  [Developer Certificate of Origin](https://developercertificate.org/)).
- **Copyleft third-party code cannot be accepted.** GPL or AGPL code cannot be
  redistributed under MIT. If a change depends on any, raise it in the issue
  before implementing rather than after.

## Questions

Open an issue or start a discussion in this repository.
