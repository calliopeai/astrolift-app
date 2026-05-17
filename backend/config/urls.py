"""config URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.2/topics/http/urls/
"""

from auth1.forms import AuthAdminForm
from auth1.sessions import Auth1SessionWorkflow
from core import views
from core.schema.views import CoreStrawberryView
from core.utils.debug import autologin
from core.utils.logger_helper import gql_logger
from django_ratelimit.decorators import ratelimit
from django.conf import settings
from django.conf.urls import include
from django.contrib import admin
from django.urls import path, re_path
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import RedirectView
from astrolift_identity.urls import (
    api_urlpatterns as identity_api_urls,
    app_urlpatterns as identity_app_urls,
)
from .schema import schema, schema_auth
from .views import app_root_view, metrics_view, root_view, test_open_telemetry

strawberry_view = CoreStrawberryView.as_view(schema=schema)
strawberry_auth_view = CoreStrawberryView.as_view(schema=schema_auth)


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
            autologin(gql_logger(ratelimit(key="user_or_ip", rate="100/m", block=True)(strawberry_view)))
        ),
    ),
    path(
        "gql/config/auth/",
        csrf_exempt(gql_logger(ratelimit(key="ip", rate="30/m", block=True)(strawberry_auth_view))),
    ),
    # GraphQL WebSocket subscriptions
    path("gql/config/ws/", csrf_exempt(CoreStrawberryView.as_view(schema=schema))),
    path("core/", include("core.urls")),
    # Token-gated audit-log export downloads (#433). Lives off the
    # operations app so the route stays close to the model it serves.
    path("", include("astrolift_operations.urls")),
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
    path(base, include(urls)),
    # Auth1 Login
    path(f"{base}auth1/", include(Auth1SessionWorkflow.urls())),
    # CLI / mobile device-flow REST surface (#475). Mounted at the
    # project root (NOT under /app/) so the wire URLs the CLI ships
    # against — POST /api/cli/v1/auth/{start,complete,refresh} —
    # work without rewriting the consumer.
    *identity_api_urls,
    re_path(r"^favicon\.ico$", favicon_view),
    path(f"{base}metrics/", metrics_view, name="metrics"),
    path("health/", include("health_check.urls")),
]

import os

# DJT URLs are gated by the same env var as the toolbar callback in
# settings.DEBUG_TOOLBAR_CONFIG. Keeping DEBUG=True in prod is useful for
# Django's own error pages, but the /__debug__/ route should stay off
# unless an operator explicitly turns it on.
if os.environ.get("ASTROLIFT_ENABLE_DJT", "").lower() in {"1", "true", "yes", "y", "on"}:
    import debug_toolbar

    urlpatterns += [
        path("__debug__/", include(debug_toolbar.urls)),
    ]
