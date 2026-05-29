"""URL routes for the Calliope App Builder REST surface (#767, #768).

Mounted at the project root from ``config.urls`` so the wire URLs the
App Builder ships against (``/api/builder/v1/dev-environments/...``)
resolve without a base-URL prefix. The endpoints carry bearer auth via
``ApiTokenAuthMiddleware`` and therefore must be ``csrf_exempt`` —
API-token callers have no CSRF token.
"""

from __future__ import annotations

from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from astrolift_lifecycle import builder_views

app_name = "astrolift_lifecycle"

urlpatterns = [
    path(
        "api/builder/v1/dev-environments/",
        csrf_exempt(builder_views.create_dev_environment),
        name="builder-dev-env-create",
    ),
    path(
        "api/builder/v1/dev-environments/<str:guid>/files/",
        csrf_exempt(builder_views.sync_dev_environment_files),
        name="builder-dev-env-files",
    ),
    path(
        "api/builder/v1/dev-environments/<str:guid>/promote/",
        csrf_exempt(builder_views.promote_dev_environment),
        name="builder-dev-env-promote",
    ),
]
