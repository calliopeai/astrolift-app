"""URL routes for the Astrolift lifecycle REST surfaces.

Mounted at the project root from ``config.urls`` so the wire URLs resolve
without a base-URL prefix. All endpoints carry bearer auth and must be
``csrf_exempt`` — API/deploy-token callers have no CSRF token.

Surfaces:

  * Calliope App Builder  (/api/builder/v1/, #767, #768)
  * CLI + CI deploy       (/api/cli/v1/,     #777)
  * Domain session handoff (/api/edge/v1/,   #1631)
"""

from __future__ import annotations

from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from astrolift_lifecycle import builder_views, cli_views, domain_handoff_views

app_name = "astrolift_lifecycle"

urlpatterns = [
    # ── Calliope App Builder (/api/builder/v1/) ──────────────────────────
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
    # ── CLI + CI deploy (/api/cli/v1/) ───────────────────────────────────
    # ``astro ci deploy`` fires POST /api/cli/v1/apps/<slug>/deploy/ with
    # a deploy token (alft_dt_...) and an image_tags dict. The response
    # includes a polling_url; the CLI polls until the status is terminal.
    path(
        "api/cli/v1/apps/<str:app_slug>/deploy/",
        csrf_exempt(cli_views.ci_deploy),
        name="cli-ci-deploy",
    ),
    path(
        "api/cli/v1/deployments/<str:guid>/status/",
        csrf_exempt(cli_views.deployment_status),
        name="cli-deployment-status",
    ),
    # CI_PUSHED static-site asset upload (#1010): sync a prebuilt bundle to
    # the workload's origin bucket + invalidate the CDN.
    path(
        "api/cli/v1/apps/<str:app_slug>/static/<str:workload>/upload/",
        csrf_exempt(cli_views.ci_static_upload),
        name="cli-static-upload",
    ),
    # ── Cross-domain session handoff (/api/edge/v1/) ─────────────────────
    # Called server-to-server by a custom domain's oauth2-proxy, never by a
    # browser: it trades the opaque id from the auth host's redirect for the
    # verified identity, so the proxy can set a first-party cookie on a
    # domain the central auth host's cookie can never reach.
    path(
        "api/edge/v1/domain-handoff/exchange/",
        csrf_exempt(domain_handoff_views.exchange_domain_handoff),
        name="domain-handoff-exchange",
    ),
]
