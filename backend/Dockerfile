FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    git \
    gcc \
    build-essential \
    cron \
    postgresql-client \
    ca-certificates \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip pipenv wheel

WORKDIR /astrolift

COPY Pipfile Pipfile.lock ./
RUN pipenv requirements --dev > requirements.txt && pip install -r requirements.txt

# whitenoise is declared in Pipfile but Pipfile.lock isn't regenerated as
# part of this change; install it explicitly so collectstatic + the
# WhiteNoiseMiddleware import work even before the lock catches up.
RUN pip install --no-cache-dir whitenoise

COPY . .

# Provider plugins (astrolift-providers, vendored under backend/vendor) ship
# with the backend image so the cluster fleet can register AWS/k8s_native
# clusters without an extra side-car. Installed in non-editable mode so the
# `astrolift.providers` entry points land in site-packages metadata and the
# loader can find them via importlib.metadata.entry_points(). Extras pulled:
# - aws  -> boto3 (already pinned in Pipfile) + kubernetes + PyYAML
# - k8s  -> kubernetes + PyYAML
# gcp/azure are intentionally omitted to keep the image small; add them as
# additional `[gcp,azure]` extras when those clouds are wanted.
RUN pip install --no-cache-dir ./vendor/astrolift-providers[aws,k8s]

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DJANGO_SETTINGS_MODULE=config.settings

# Pre-collect static files into STATIC_ROOT so WhiteNoise can serve them at
# runtime. Without this, /static/admin/css/* etc. would 404 in production
# because Django's contrib.staticfiles isn't a runtime filesystem walker.
# DJANGO_CONFIGURATION is forced to a config that uses the Manifest storage
# so the hashing/compression step runs at build time, not first request.
RUN DJANGO_CONFIGURATION=Prd \
    DJANGO_SECRET_KEY=build-time-noop \
    POSTGRES_ENGINE=django.db.backends.dummy \
    python manage.py collectstatic --noinput

CMD ["python", "startup.py"]
