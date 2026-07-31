#!/bin/bash
# Smoke-check that the Strawberry schema still builds (no DB required).
docker run --rm --network docker_default \
  -v /Users/ragelink/repos/calliope/astrolift/astrolift-app/backend:/astrolift -w /astrolift \
  -e DJANGO_SETTINGS_MODULE=config.settings -e POSTGRES_HOST=postgres-local \
  -e POSTGRES_PORT=5432 -e POSTGRES_USER=dbadmin -e POSTGRES_PASSWORD=Password123 \
  -e POSTGRES_DB=astrolift -e POSTGRES_ENGINE=django.db.backends.postgresql \
  -e REDIS_HOST=redis-local \
  astrolift-local sh -lc 'unset DJANGO_CONFIGURATION; python -c "
import django; django.setup()
from config.schema import schema
sdl = schema.as_str()
print(\"SCHEMA OK\", len(sdl), \"chars\")
" 2>&1 | tail -25'
