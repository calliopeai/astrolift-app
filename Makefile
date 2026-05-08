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
        migrate migrations seed superuser schema codegen \
        test fmt lint typecheck \
        perms ui-install ui-dev ui-build

help:
	@echo "Astrolift — common targets"
	@echo
	@echo "  Stack:     up | build | down | logs | logs-ui | shell | shell-ui | ps"
	@echo "  Backend:   migrate | migrations | seed | superuser | schema | perms"
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
	$(COMPOSE) exec $(CONTAINER) $(PYTHON) -c "import django; django.setup() if False else None; \
from config.schema import schema; \
open('/astrolift/schema.graphql','w').write(schema.as_str())"
	cp backend/schema.graphql frontend/schema.graphql
	@echo "→ backend/schema.graphql + frontend/schema.graphql"

# Regenerate TS types from the dumped schema. Run schema first if
# the backend changed, then this; or `make codegen-all` for both.
codegen:
	$(COMPOSE) exec $(UI_CONTAINER) npm run codegen

codegen-all: schema codegen

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
