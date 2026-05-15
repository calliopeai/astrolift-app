"""
Test-only Django settings (#396).

Inherits the full production settings and replaces a few runtime-only
backends so the test suite doesn't drag in optional runtime deps:

  - STORAGES: drop the ``whitenoise`` static-files backend (test
    container doesn't install whitenoise — it's a deploy-time dep).
    Falls back to Django's stock FileSystemStorage which is fine
    for tests (no test serves static assets).
  - DEBUG_TOOLBAR: stays in INSTALLED_APPS because the upstream
    settings module wires it conditionally; the test STORAGES patch
    is enough to get past app loading.

If a test genuinely needs the production storage backend (e.g.
covering an S3 upload code path), it can fixture-override
``settings.STORAGES`` per-test rather than the whole suite paying
the dep cost.
"""

from __future__ import annotations

from config.settings import *  # noqa: F401, F403

# Plain in-memory defaults — no whitenoise, no s3boto3. Tests that
# need to exercise either should override this fixture-scoped.
STORAGES = {  # noqa: F811
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# Drop whitenoise from MIDDLEWARE too — when a test spins up the
# WSGI app (e.g. via Django ``Client`` for webhook tests), the
# middleware list is what gets walked, and whitenoise's
# ``WhiteNoiseMiddleware.__init__`` tries to import the package
# even if STORAGES no longer points at it.
MIDDLEWARE = [  # noqa: F811
    m
    for m in MIDDLEWARE  # type: ignore[name-defined]  # noqa: F405
    if "whitenoise" not in m
]
