# astrolift-app

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Django](https://img.shields.io/badge/django-5.x-092E20.svg)](https://www.djangoproject.com/)
[![Next.js](https://img.shields.io/badge/next.js-16-000000.svg)](https://nextjs.org/)
[![Strawberry](https://img.shields.io/badge/strawberry-graphql-E91E63.svg)](https://strawberry.rocks/)
[![Temporal](https://img.shields.io/badge/temporal-workflows-7E22CE.svg)](https://temporal.io/)

The **Astrolift control plane** — backend (Django + Strawberry GraphQL +
Temporal) and frontend (Next.js + React 19 + Apollo + Tailwind) living
together in one repo so a single PR can ship a feature end-to-end.

> **Status posture (be honest about it).** Astrolift is **pre-1.0**.
> The control-plane shape is stable and the local dev story works
> end-to-end (compose stack: api, ui, postgres, redis, temporal,
> mailpit, optional minio + opensearch). Most surfaces — multi-cloud
> driver protocol, GitOps delivery, observability, RBAC, billing,
> approvals, alerts — are implemented; some are still iterating
> against the spec set in the parent metarepo. Expect breaking
> GraphQL changes between minor versions until 1.0.

---

## What this is (and isn't)

| | |
|---|---|
| **astrolift-app** *(this repo)* | The control plane. Manages tenant orgs, users, clusters, app deployments, secrets, webhooks, alerts, billing, GitOps state. **Does not run tenant workloads.** |
| **[astrolift-opscode](https://github.com/calliopeai/astrolift-opscode)** | Terraform + Helm IaC that **hosts** this control plane on AWS / GCP / Azure / vanilla Kubernetes. Fork it, set a few config values, and you have a working install. |
| **astrolift-cli** | Operator + tenant-developer CLI (`astro …`). Talks to this control plane via GraphQL + REST. |
| **astrolift-providers** | Per-cloud driver plugins (EKS / GKE / AKS / k8s-native) implementing the typed driver protocol. May remain private as it stabilizes. |
| **[astrolift-docs](https://astrolift.dev)** | Public documentation. The marketing + reference site. |

If you want to **run** Astrolift on a cloud you control, start at
`astrolift-opscode`. If you want to **modify** the control plane
itself — auth model, GraphQL schema, workflows, UI — you're in the
right place.

---

## Architecture in one paragraph

A single Astrolift "install" = one DNS zone + one database. The control
plane is a horizontally-scalable set of Django + Strawberry GraphQL
processes plus a fleet of Temporal workers handling long-running
workflows (onboarding, deploy, rollback, GitOps reconciliation). The
frontend is a Next.js 16 app that renders against the same GraphQL
schema the CLI uses. The control plane runs on whatever container
runtime each cloud prefers (ECS Fargate on AWS, Cloud Run on GCP,
Container Apps on Azure, in-cluster on k8s-native). It manages
tenant clusters across one or more clouds via a typed driver
protocol — full topology is in
[`bootstrap.md`](bootstrap.md) and the spec set in the parent
metarepo.

---

## Quick start (local dev)

Prerequisites: Docker Desktop (or Docker Engine + compose v2).

```bash
./bootstrap.sh        # one-time: env files, deps check
./run.sh up           # start the full compose stack
./run.sh seed         # seed dev identity (acme/eng/api/dev@local.astrolift.net)
./run.sh urls         # print local URLs
```

URLs after `./run.sh up`:

- Frontend     — http://localhost:3000
- Backend      — http://localhost:8000/app/
- GraphQL      — http://localhost:8000/app/gql/config/
- Admin        — http://localhost:8000/app/admin/
- Temporal UI  — http://localhost:8233
- Mailpit      — http://localhost:8025

Common commands (`./run.sh help` for the full list):

```bash
./run.sh shell        # bash in the backend container
./run.sh logs         # tail backend logs
./run.sh test         # pytest -x against real Postgres + Temporal
./run.sh down         # stop the stack
```

---

## Layout

```
backend/        Django + Strawberry GraphQL + Temporal — the control plane
  astrolift_*/  Domain apps (clusters, identity, drivers, manifest,
                workflows, billing, compliance, registry, scm, ...)
  core/         Shared base models (Tracking, soft delete), admin,
                middleware, permissions catalog
  config/       Django settings, schema merge, telemetry
  auth1/        Session-based auth + dev-login bypass

frontend/       Next.js 16 + React 19 + Apollo + Tailwind v4 + shadcn
  app/          App-router routes
  components/   Reusable UI
  graphql/      Apollo client + codegen output
  hooks/        Shared React hooks

docker/         docker-compose + entrypoint + runtime config
docs/           Engineering docs (operator-facing)
schema.graphql  GraphQL contract — backend writes it, frontend codegens against it
bootstrap.md    Repo conventions (read first)
Makefile        Top-level orchestration
run.sh          Dev command center
```

---

## Testing

The backend test suite runs against **real Postgres and a real Temporal
test environment** — not mocks. The compose stack provides both.

```bash
./run.sh test                         # pytest -x in the backend container
make test                             # equivalent

# Lint / typecheck
cd backend && ruff format --check . && ruff check . && mypy core config

# Frontend lint + typecheck (run inside the ui container)
docker compose -f docker/docker-compose.yaml exec ui npm run lint
docker compose -f docker/docker-compose.yaml exec ui npx tsc --noEmit
```

Schema + codegen flow when the GraphQL contract changes:

```bash
make schema           # dump backend/schema.graphql + copy to frontend/
make codegen          # regenerate TS types from the dumped schema
make codegen-all      # both, in order
```

---

## Configuration

Local dev: `bootstrap.sh` copies `backend/config/example.env` to
`backend/config/local.env` on first run. Edit values there. The
`local.env` file is gitignored — never commit real credentials.

For non-local environments, configuration comes from environment
variables (12-factor). The full list lives in `backend/config/example.env`
with comments. Headlines:

| Group | What | Notes |
|---|---|---|
| Django core | `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, CORS settings | Required |
| Database | `POSTGRES_*` | Required |
| Cache | `DJANGO_CACHE_URL` (Redis) | Required |
| Auth0 | `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`, `AUTH0_AUDIENCE` | Required for non-local; local uses dev-login bypass when `DEBUG=True` |
| Storage | `DJANGO_DEFAULT_FILE_STORAGE`, `GOOGLE_APPLICATION_CREDENTIALS` | Optional — defaults to local filesystem |
| Email | `EMAIL_*`, anymail backend | Optional — local uses Mailpit |
| Sentry | `SENTRY_DSN` | Optional |
| OpenTelemetry | `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME` | Optional — falls back to console exporter |

---

## Documentation

- **[bootstrap.md](bootstrap.md)** — repo conventions (read first)
- **[backend/bootstrap.md](backend/bootstrap.md)** — backend layer conventions
- **[frontend/bootstrap.md](frontend/bootstrap.md)** — frontend layer conventions
- **[docs/](docs/)** — operator-facing engineering docs
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — fork + PR flow + style requirements
- **[SECURITY.md](SECURITY.md)** — vulnerability disclosure
- **[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)** — community standards
- **Public docs** — [astrolift.dev](https://astrolift.dev)

---

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow,
testing requirements, and the conventions that are non-negotiable
(soft delete, MutationResult envelope, permission-first resolvers,
real-DB tests, no rebases, no co-author trailers).

---

## License

MIT. See [LICENSE](LICENSE).

Copyright (c) 2026 Calliope Labs Inc. Calliope AI and Astrolift are trademarks
of Calliope Labs Inc.

Portions of the framework underlying this repo are derived from **[boilerworks-django-nextjs](https://github.com/ConflictHQ/boilerworks-django-nextjs)** (Copyright (c) Conflict LLC, MIT-licensed). Tip of the hat 🎩
