"""config URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.2/topics/http/urls/
"""

import os

from django.conf import settings
from django.conf.urls import include
from django.contrib import admin
from django.urls import path, re_path
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import RedirectView
from django_ratelimit.decorators import ratelimit

from astrolift_agents.urls import urlpatterns as agents_api_urls
from astrolift_clusters.urls import urlpatterns as clusters_api_urls
from astrolift_dispatch.urls import urlpatterns as dispatch_api_urls
from astrolift_identity.urls import (
    api_urlpatterns as identity_api_urls,
)
from astrolift_identity.urls import (
    app_urlpatterns as identity_app_urls,
)
from astrolift_identity.urls import (
    scim_api_urlpatterns as identity_scim_urls,
)
from astrolift_lifecycle.urls import urlpatterns as lifecycle_api_urls
from astrolift_pipelines.urls import urlpatterns as pipeline_webhook_urls
from astrolift_scm.urls import urlpatterns as scm_webhook_urls
from auth1.forms import AuthAdminForm
from auth1.sessions import Auth1SessionWorkflow
from core import views
from core.schema.views import CoreStrawberryView
from core.utils.debug import autologin
from core.utils.logger_helper import gql_logger
from core.views_well_known import apple_app_site_association, assetlinks_json

from .schema import schema, schema_auth
from .views import app_root_view, metrics_view, root_view, test_open_telemetry

strawberry_view = CoreStrawberryView.as_view(schema=schema)
strawberry_auth_view = CoreStrawberryView.as_view(schema=schema_auth)


def support_tickets_view(request):
    # Keep the support module import lazy so the default-off feature does not
    # add upstream integration imports during ordinary URL setup.
    from core.views_client_cove_support import support_tickets

    return support_tickets(request)


def trigger_error(request):
    return 1 / 0


admin.autodiscover()
admin.site.login_form = AuthAdminForm
admin.site.login_template = "admin/auth1_login.html"
admin.site.site_header = "Astrolift"
admin.site.site_title = f"Astrolift Admin {settings.VERSION}"
admin.site.index_title = "Welcome to Astrolift"

urls = [
    path("", app_root_view),
    path("admin/", admin.site.urls),
    path("nested_admin/", include("nested_admin.urls")),
    path("sentry-debug/", trigger_error),
    path("export/", views.download_file),
    # GraphQL endpoints (Strawberry) — rate limited
    path(
        "gql/config/",
        csrf_exempt(
            autologin(
                gql_logger(
                    ratelimit(
                        key="user_or_ip",
                        rate=settings.RATELIMIT_GRAPHQL_RATE,
                        block=True,
                    )(strawberry_view)
                )
            )
        ),
    ),
    path(
        "gql/config/auth/",
        csrf_exempt(
            gql_logger(
                ratelimit(
                    key="ip",
                    rate=settings.RATELIMIT_GRAPHQL_AUTH_RATE,
                    block=True,
                )(strawberry_auth_view)
            )
        ),
    ),
    # GraphQL WebSocket subscriptions
    path("gql/config/ws/", csrf_exempt(CoreStrawberryView.as_view(schema=schema))),
    path("core/", include("core.urls")),
    # Token-gated audit-log export downloads (#433). Lives off the
    # operations app so the route stays close to the model it serves.
    path("", include("astrolift_operations.urls")),
    # SES → SNS event webhook receiver (#756). Mounted off the
    # services app so the route stays close to the EmailEvent model
    # it appends to. Unauthenticated by design — SNS posts without
    # bearer auth; the trust model is that this URL is only
    # subscribed to a platform-owned SNS topic.
    path("", include("astrolift_services.urls")),
    # CLI / mobile device-flow approval page (#475). Mounted under
    # /app/ so auth1's @login_required redirects unauth'd browsers
    # into the IdP just like every other operator surface.
    *identity_app_urls,
    path("test/open_telemetry/", test_open_telemetry, name="test-open-telemetry"),
]

base = settings.BASE_URL
favicon_view = RedirectView.as_view(url="/static/favicon.ico", permanent=True)

urlpatterns = [
    path("", root_view),
    # Mobile universal-link / app-link well-known files (#541). Mounted
    # at the project root (NOT under BASE_URL) so iOS / Android fetch
    # them from the apex host the OS verifier expects:
    # https://app.astrolift.dev/.well-known/apple-app-site-association
    # https://app.astrolift.dev/.well-known/assetlinks.json
    # These MUST stay anonymous-accessible — the OS verifier doesn't
    # carry a session — so they sit ahead of the ``base`` include that
    # routes most app traffic through auth1.
    path(".well-known/apple-app-site-association", apple_app_site_association),
    path(".well-known/assetlinks.json", assetlinks_json),
    path(base, include(urls)),
    # Auth1 Login
    path(f"{base}auth1/", include(Auth1SessionWorkflow.urls())),
    # CLI / mobile device-flow REST surface (#475). Mounted at the
    # project root (NOT under /app/) so the wire URLs the CLI ships
    # against — POST /api/cli/v1/auth/{start,complete,refresh} —
    # work without rewriting the consumer.
    *identity_api_urls,
    # SCIM 2.0 provisioning surface (#78, #91). Mounted at the project
    # root because ``/api/scim/v2/`` is the base URL the operator hands
    # to Okta / Entra; the IdP arrives with an org-scoped ``alft_st_``
    # bearer and no session, so the ``/app/`` prefix (and its auth1
    # login gate) would turn every provisioning call into a redirect.
    *identity_scim_urls,
    # Calliope App Builder REST surface (#767, #768). Mounted at the
    # project root for the same reason as the CLI flow — the App
    # Builder ships against ``/api/builder/v1/dev-environments/...``
    # as a stable wire contract.
    *lifecycle_api_urls,
    # Agent Dispatch REST surface (#58). Mounted at the project root so
    # the wire URL — ``/api/agents/v1/tasks/<id>/vnc/ws/`` — resolves
    # without the ``/app/`` prefix. The noVNC proxy stub returns 501
    # until ASGI/Channels is configured; the URL is registered now so
    # the front-end can target a stable endpoint.
    *agents_api_urls,
    # SCM webhook receivers (#778). Mounted at the project root because
    # the wire URLs the source host (GitHub / GitLab) was told to POST
    # to — ``/api/webhooks/<kind>/<app_guid>/`` — must resolve without
    # the ``/app/`` prefix that ``base`` adds to authenticated UI
    # traffic. Each view performs its own HMAC verification before
    # touching the request body.
    *scm_webhook_urls,
    # Pipeline webhooks + runner API + Dispatch Service API
    *pipeline_webhook_urls,   # includes /webhooks/pipelines/*, /api/pipelines/v1/runners/*
    *dispatch_api_urls,       # /api/dispatch/v1/*
    # In-cluster keep-alive agent heartbeat ingest (#808). Mounted at
    # the project root so the agent's wire URL —
    # /api/clusters/v1/<guid>/heartbeat/ — resolves without the /app/
    # prefix. Scoped-Bearer-key auth, same shape as the runner/dispatch
    # REST surfaces above.
    *clusters_api_urls,       # /api/clusters/v1/<guid>/heartbeat/
    path("api/support/v1/tickets/", support_tickets_view, name="support-tickets"),
    re_path(r"^favicon\.ico$", favicon_view),
    path(f"{base}metrics/", metrics_view, name="metrics"),
    path("health/", include("health_check.urls")),
]

# DJT URLs are gated by the same env var as the toolbar callback in
# settings.DEBUG_TOOLBAR_CONFIG. Keeping DEBUG=True in prod is useful for
# Django's own error pages, but the /__debug__/ route should stay off
# unless an operator explicitly turns it on.
if os.environ.get("ASTROLIFT_ENABLE_DJT", "").lower() in {"1", "true", "yes", "y", "on"}:
    import debug_toolbar

    urlpatterns += [
        path("__debug__/", include(debug_toolbar.urls)),
    ]
