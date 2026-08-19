# Astrolift — top-level developer Makefile.
#
# Backend (Django) + Frontend (Next.js) live as siblings under one repo.
# All container operations go through ``docker/docker-compose.yaml``.

DC ?= docker compose
COMPOSE_FILE ?= docker/docker-compose.yaml
COMPOSE := $(DC) -f $(COMPOSE_FILE)
CONTAINER ?= astrolift-local
UI_CONTAINER ?= ui
PYTHON ?= python
CONTRACT_ENV := -e FEATURE_AGENTS=true -e FEATURE_WORKFLOWS=true \
                -e FEATURE_TEMPORAL=true -e FEATURE_OPENSEARCH=true \
                -e FEATURE_FILE_UPLOADS=true -e FEATURE_DEPLOY_PIPELINE=true

.PHONY: help up build down logs logs-ui shell shell-ui ps \
        migrate migrations seed superuser schema contracts contracts-check codegen \
        test fmt lint typecheck \
        perms ui-install ui-dev ui-build

help:
	@echo "Astrolift — common targets"
	@echo
	@echo "  Stack:     up | build | down | logs | logs-ui | shell | shell-ui | ps"
	@echo "  Backend:   migrate | migrations | seed | superuser | schema | contracts | contracts-check | perms"
	@echo "  Frontend:  ui-install | ui-dev | ui-build | codegen | codegen-all"
	@echo "  Quality:   test | fmt | lint | typecheck"
	@echo
	@echo "  ./run.sh up   — preferred entry point with health checks + URLs"

# ---- stack ---------------------------------------------------------

up:
	$(COMPOSE) up -d

build:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f $(CONTAINER)

logs-ui:
	$(COMPOSE) logs -f $(UI_CONTAINER)

ps:
	$(COMPOSE) ps

shell:
	$(COMPOSE) exec $(CONTAINER) bash

shell-ui:
	$(COMPOSE) exec $(UI_CONTAINER) sh

# ---- backend (django) ----------------------------------------------

migrate:
	$(COMPOSE) exec $(CONTAINER) $(PYTHON) manage.py migrate

migrations:
	$(COMPOSE) exec $(CONTAINER) $(PYTHON) manage.py makemigrations

seed:
	$(COMPOSE) exec $(CONTAINER) $(PYTHON) manage.py seed_dev_identity

superuser:
	$(COMPOSE) exec $(CONTAINER) $(PYTHON) manage.py createsuperuser

# Dump the live Strawberry schema and copy it next to the frontend
# so codegen can run without the backend container being up.
schema:
	$(COMPOSE) exec $(CONTRACT_ENV) $(CONTAINER) $(PYTHON) manage.py export_contracts
	cp backend/schema.graphql frontend/schema.graphql
	@echo "→ backend/schema.graphql + frontend/schema.graphql"

contracts: schema
	@echo "→ backend/contracts/mcp-tools.json"

contracts-check:
	$(COMPOSE) exec $(CONTRACT_ENV) $(CONTAINER) $(PYTHON) manage.py export_contracts --check
	cmp backend/schema.graphql frontend/schema.graphql

# Regenerate TS types from the dumped schema. Run schema first if
# the backend changed, then this; or `make codegen-all` for both.
codegen:
	$(COMPOSE) exec $(UI_CONTAINER) npm run codegen

codegen-all: schema codegen

# Render the frontend's permission-slug mirror from core.permissions.
# The backend container mounts only ./backend, so the generator prints the
# module and the host writes it — same split as `schema`.
perms:
	$(COMPOSE) exec -T $(CONTAINER) $(PYTHON) manage.py make_perms --print \
		> frontend/lib/permissions/permissions.generated.ts
	@echo "→ frontend/lib/permissions/permissions.generated.ts"

# ---- frontend (next.js) --------------------------------------------

ui-install:
	$(COMPOSE) exec $(UI_CONTAINER) npm install

ui-dev:
	$(COMPOSE) exec $(UI_CONTAINER) npm run dev

ui-build:
	$(COMPOSE) exec $(UI_CONTAINER) npm run build

# ---- quality -------------------------------------------------------

# Tests run inside the compose stack so they get a real Postgres + real
# Temporal test environment.
test:
	# pytest-django imports `configurations` whenever DJANGO_CONFIGURATION
	# is set; we use plain Django settings now, so unset it for the test
	# run. Settings.py reads it as a label too — falls back to default.
	# Test-only settings drop the whitenoise static-files backend so the
	# suite doesn't drag in a runtime-only dep (#396).
	$(COMPOSE) exec -e DJANGO_SETTINGS_MODULE=config.test_settings $(CONTAINER) sh -lc 'unset DJANGO_CONFIGURATION; pytest -x'

fmt:
	cd backend && ruff format . && ruff check --fix .

lint:
	cd backend && ruff check . && ruff format --check .
	cd frontend && npm run lint

typecheck:
	cd backend && mypy core config
