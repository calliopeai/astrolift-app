# astrolift-api bootstrap

## What This Is

The Astrolift API backend. A Django 5 + Strawberry GraphQL + DRF service that hosts the control plane for the Astrolift platform: app registry, deployments, workflow orchestration, multi-org RBAC, and provider plugin management.

This codebase extends the boilerworks-django-nextjs scaffold patterns: `Tracking` / `BaseCoreModel` ORM bases, `MutationResult { ok, errors, data? }` envelope, group-based permissions, resolver-entry permission checks, schema-merge pattern, and production-grade Django settings.

## Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Framework | Django 5 |
| GraphQL | Strawberry + strawberry-graphql-django |
| REST | Django REST Framework (webhooks, CLI endpoints) |
| Workflow runtime | Temporal (Python SDK) |
| Database | Postgres 16 |
| Cache | Redis 7 |
| Search | OpenSearch 2 |
| Object storage | S3-compatible (MinIO local, S3/GCS prod) |
| Email | django-ses (prod), Mailpit (local) |
| Auth | Auth0 + session-based (auth1 app) |
| Observability | OpenTelemetry, ECS logging, Sentry |

## Running Locally

```bash
# Start the stack (from docker/)
docker compose up -d

# Or with optional profiles
docker compose --profile search --profile storage --profile monitoring up -d

# Run migrations
docker exec astrolift-local python manage.py migrate

# Seed dev data
docker exec astrolift-local python manage.py seed

# Create superuser
docker exec astrolift-local python manage.py createsuperuser
```

## Ports (local)

| Service | URL |
|---|---|
| Django API | http://localhost:8000 |
| Django Admin | http://localhost:8000/app/admin/ |
| GraphQL | http://localhost:8000/app/gql/config/ |
| Health | http://localhost:8000/health/ |
| Temporal UI | http://localhost:8233 |
| Mailpit | http://localhost:8025 |
| Postgres | localhost:5432 |
| Redis | localhost:6379 |
| MinIO Console | http://localhost:9001 |

## Common Commands

```bash
make up           # Start the stack
make build        # Build and start
make down         # Stop the stack
make migrate      # Run migrations
make migrations   # Create new migrations
make seed         # Load dev fixtures
make test         # Run tests
make lint         # Run flake8 + isort checks
make schema       # Export GraphQL SDL
make shell        # Shell into Django container
```

### Concurrent migrations

`manage.py migrate` serializes PostgreSQL invocations with a database-scoped
session advisory lock, including web startup and the installer's bootstrap task.
The lock covers Django's migration planning, execution, and post-migrate hooks;
it releases when the command finishes or fails. Normal Django options, including
`--database`, still apply. Non-PostgreSQL databases use Django's usual behavior.
Both callers must run an application image containing this command (#1739;
calliopeai/calliope-installer#304).

## Conventions

- **Models**: Inherit from `Tracking` (audit) or `BaseCoreModel` (named entities with guid/slug)
- **Soft deletes**: Set `deleted_at`, never call `.delete()` on business objects
- **GraphQL**: Per-app `schema/types.py`, `queries.py`, `mutations.py`. Merged in `config/schema.py`
- **Mutations**: Always return `MutationResult { ok, errors, data? }`, never raise
- **Permissions**: Check at the top of every resolver and mutation -- no exceptions
- **Admin**: Inherit from `BaseCoreAdmin`
- **Tests**: Use `schema.execute_sync()` for GraphQL, real database (no mocks)
- **Workflows**: Temporal SDK in `workflows/temporal/`. One worker process, scales horizontally
- **Feature toggles**: Via `config/features.py` and environment variables
- **Domain app discovery**: Apps with `astrolift_config/settings.py` are auto-discovered

## Adding a New App

```bash
python manage.py startapp myapp
# Then: add to INSTALLED_APPS, create schema/, wire into config/schema.py, make migrations
```
