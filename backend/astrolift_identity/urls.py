"""URL routes owned by ``astrolift_identity``.

Currently exposes the CLI / mobile device-flow surface (#475), the SCIM
2.0 provisioning surface (#78, #91) and the SSO step-up re-auth flow
(#526). The REST endpoints (``/api/cli/v1/auth/{start,complete,refresh}``
and ``/api/scim/v2/*``) are mounted at the project root by
``config.urls``; the in-browser approval page and the SSO step-up
endpoints live under the auth1-protected ``/app/`` prefix.
"""

from __future__ import annotations

from django.urls import path
from django_ratelimit.decorators import ratelimit

from astrolift_identity import device_flow_views, scim_views, step_up_sso
from astrolift_identity.scim import SCIM_RATE_LIMIT_PER_MIN

app_name = "astrolift_identity"


# Public, no-auth REST surface for the CLI. The /start endpoint is
# the rate-limit hot spot (an attacker could mint thousands of pending
# rows otherwise). /complete + /refresh are rate-limited *per session*
# in the business layer (see ``device_flow.MIN_POLL_INTERVAL``); the
# IP-level limit here is a coarse second line.
api_urlpatterns = [
    path(
        "api/cli/v1/auth/start",
        ratelimit(key="ip", rate="10/m", block=True)(device_flow_views.device_flow_start),
        name="device-flow-start",
    ),
    path(
        "api/cli/v1/auth/complete",
        ratelimit(key="ip", rate="120/m", block=True)(device_flow_views.device_flow_complete),
        name="device-flow-complete",
    ),
    path(
        "api/cli/v1/auth/refresh",
        ratelimit(key="ip", rate="60/m", block=True)(device_flow_views.device_flow_refresh),
        name="device-flow-refresh",
    ),
]


# SCIM 2.0 provisioning surface (#78, #91). Mounted at the project root
# by config.urls because ``/api/scim/v2/`` is the base URL an operator
# pastes into Okta / Entra, and RFC 7644 fixes the resource paths
# (no trailing slash). Auth is the org-scoped ``alft_st_`` bearer,
# checked inside the view — no session, no CSRF.
#
# The ceiling is spec 27 §8's 240/min, keyed on the credential rather
# than the IP so one tenant's IdP (or a NAT they share) cannot starve
# another's, and so the constant that documents the limit is the
# constant that enforces it.
_scim_ratelimit = ratelimit(
    key="header:authorization",
    rate=f"{SCIM_RATE_LIMIT_PER_MIN}/m",
    block=True,
)

scim_api_urlpatterns = [
    path(
        "api/scim/v2/Users",
        _scim_ratelimit(scim_views.scim_users),
        name="scim-users",
    ),
    path(
        "api/scim/v2/Users/<str:member_guid>",
        _scim_ratelimit(scim_views.scim_user_detail),
        name="scim-user-detail",
    ),
]


# Auth1 session-cookie-protected approval surface. Mounted under the
# ``/app/`` prefix (BASE_URL) by config.urls so the login_required
# middleware redirects unauth'd users to the configured frontend login entry.
app_urlpatterns = [
    path(
        "cli/auth/device/<str:session_guid>/",
        device_flow_views.device_flow_approval,
        name="device-flow-approval",
    ),
    # SSO step-up re-auth (#526). Lives under /app/ so the auth1 login
    # gate enforces an authenticated session on entry; the callback
    # validates ``state`` + ``auth_time`` itself. The names are
    # referenced by ``elevate_sso_start.reverse()`` so keep them
    # stable — a rename here breaks the redirect.
    path(
        "auth1/elevate-sso/",
        step_up_sso.elevate_sso_start,
        name="auth1-elevate-sso-start",
    ),
    path(
        "auth1/elevate-sso/callback/",
        step_up_sso.elevate_sso_callback,
        name="auth1-elevate-sso-callback",
    ),
]
