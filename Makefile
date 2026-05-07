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

.PHONY: help up build down logs logs-ui shell shell-ui ps \
        migrate migrations seed superuser schema \
        test fmt lint typecheck \
        perms ui-install ui-dev ui-build

help:
	@echo "Astrolift — common targets"
	@echo
	@echo "  Stack:     up | build | down | logs | logs-ui | shell | shell-ui | ps"
	@echo "  Backend:   migrate | migrations | seed | superuser | schema | perms"
	@echo "  Frontend:  ui-install | ui-dev | ui-build"
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

# Schema lives at the top level so the frontend can codegen against it.
schema:
	$(COMPOSE) exec $(CONTAINER) $(PYTHON) manage.py export_schema config.schema:schema --path /astrolift/../schema.graphql 2>/dev/null || \
		$(COMPOSE) exec $(CONTAINER) $(PYTHON) manage.py export_schema config.schema:schema --path schema.graphql
	@if [ -f backend/schema.graphql ]; then cp backend/schema.graphql schema.graphql; fi
	@echo "→ schema.graphql"

perms:
	$(COMPOSE) exec $(CONTAINER) $(PYTHON) manage.py make_perms

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
	$(COMPOSE) exec -e DJANGO_SETTINGS_MODULE=config.settings $(CONTAINER) pytest -x

fmt:
	cd backend && ruff format . && ruff check --fix .

lint:
	cd backend && ruff check . && ruff format --check .
	cd frontend && npm run lint

typecheck:
	cd backend && mypy core config
