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

# pynacl: same story — declared in Pipfile (#383) for the libsodium
# sealed-box used by the GitHub CI-secrets push, but the lock isn't
# regenerated here, so install it explicitly.
RUN pip install --no-cache-dir pynacl

# pytest-asyncio: declared in Pipfile dev-packages; some images were
# built before the lock caught up, so install it explicitly. Required
# for any test with `async def` bodies (pubsub, observability, etc.) —
# pytest.ini sets ``asyncio_mode = auto`` so the plugin must be loaded.
RUN pip install --no-cache-dir pytest-asyncio

# tomli_w: declared in Pipfile for the manifest auto-discoverer
# (#119) which emits inferred TOML; the lock isn't regenerated in
# tandem on every image build, so install explicitly the same way
# pynacl / pytest-asyncio are handled above. Backend tests for
# astrolift_manifest + astrolift_services import it at module
# scope.
RUN pip install --no-cache-dir tomli_w

# starlette: declared in Pipfile for the ASGI subscription path
# (core/exec_ws) — strawberry.asgi imports it unconditionally. Same
# install-explicitly pattern until the lock catches up.
RUN pip install --no-cache-dir starlette

# moto: declared in Pipfile dev-packages (mirrors providers'[dev] extra)
# for the AWS-faking test surface (providers/tests/aws + the astrolift_*
# EKS-token / managed-service tests). Same install-explicitly-until-the-
# lock-catches-up pattern as above; without it the full suite errors on
# ``import moto``. Extras match the Pipfile entry. (``kubernetes`` is
# already pulled in below via ``providers[aws,k8s]``.)
RUN pip install --no-cache-dir "moto[s3,iam,sts,ec2,eks,route53,acm,ecr,secretsmanager,ssm,sqs,sns,dynamodb,elasticache,rds,efs]>=5.0"

COPY . .

# Provider plugins live in-tree at backend/providers (formerly vendored under
# backend/vendor/astrolift-providers; the upstream repo was archived after the
# subtree merge that brought its full history into this repo). Installed in
# non-editable mode so the `astrolift.providers` entry points land in
# site-packages metadata and the loader can find them via
# importlib.metadata.entry_points(). Extras pulled:
# - aws  -> boto3 (already pinned in Pipfile) + kubernetes + PyYAML
# - k8s  -> kubernetes + PyYAML
# gcp/azure are intentionally omitted to keep the image small; add them as
# additional `[gcp,azure]` extras when those clouds are wanted.
RUN pip install --no-cache-dir ./providers[aws,k8s]

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
