# Astrolift API — developer Makefile.
#
# Targets are intentionally thin: each one is a single command we run a lot.
# All container operations go through ``docker/docker-compose.yaml``.
#
# Pass ``DC`` to override the compose binary (``docker compose`` vs
# ``docker-compose``).

DC ?= docker compose
COMPOSE_FILE ?= docker/docker-compose.yaml
COMPOSE := $(DC) -f $(COMPOSE_FILE)
CONTAINER ?= astrolift-local
PYTHON ?= python

.PHONY: help up build down logs shell ps \
        migrate migrations seed superuser schema \
        dev test fmt lint typecheck \
        perms

help:
	@echo "Astrolift API — common targets"
	@echo
	@echo "  Stack:        up | build | down | logs | shell | ps"
	@echo "  Database:     migrate | migrations | seed | superuser"
	@echo "  Quality:      fmt | lint | typecheck | test"
	@echo "  Codegen:      schema | perms"
	@echo "  Dev loop:     dev (up, migrate, runserver)"

# ---- stack ---------------------------------------------------------

up:
	$(COMPOSE) up -d

build:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f $(CONTAINER)

ps:
	$(COMPOSE) ps

shell:
	$(COMPOSE) exec $(CONTAINER) bash

# ---- django --------------------------------------------------------

migrate:
	$(COMPOSE) exec $(CONTAINER) $(PYTHON) manage.py migrate

migrations:
	$(COMPOSE) exec $(CONTAINER) $(PYTHON) manage.py makemigrations

seed:
	$(COMPOSE) exec $(CONTAINER) $(PYTHON) manage.py seed

superuser:
	$(COMPOSE) exec $(CONTAINER) $(PYTHON) manage.py createsuperuser

schema:
	$(COMPOSE) exec $(CONTAINER) $(PYTHON) manage.py export_schema config.schema:schema --path schema.graphql

# ---- quality -------------------------------------------------------

# pytest is the canonical test runner; it picks up the marker plugin
# defined in ``pytest.ini``. Tests run inside the compose stack so they
# get a real Postgres + real Temporal test environment.
test:
	$(COMPOSE) exec -e DJANGO_SETTINGS_MODULE=config.settings $(CONTAINER) pytest -x

fmt:
	ruff format .
	ruff check --fix .

lint:
	ruff check .
	ruff format --check .

typecheck:
	mypy core config

# ---- codegen -------------------------------------------------------

# Permission catalog is the source of truth for resolver checks; this
# regenerates the typed enum from config/permissions.py.
perms:
	$(COMPOSE) exec $(CONTAINER) $(PYTHON) manage.py make_perms

# ---- composite -----------------------------------------------------

dev: up
	$(COMPOSE) exec $(CONTAINER) $(PYTHON) manage.py migrate --noinput
	$(COMPOSE) exec $(CONTAINER) $(PYTHON) manage.py runserver 0.0.0.0:8000
