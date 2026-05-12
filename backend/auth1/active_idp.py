"""
Public ``GET /app/auth1/active-idp.json`` endpoint.

The login screen calls this *before* the user has authenticated to know
which CTA to render — "Continue with Auth0", "Continue with Cognito",
or a username/password form. The response is intentionally narrow:
``kind`` + ``displayName`` + ``loginPath`` only. No client secrets, no
config that could leak credentials, no per-user data.

If no organization has a primary IdP bound (fresh install), the
response describes the ``local`` fallback. The local backend is
gated by ``settings.DEBUG`` plus an explicit
``ASTROLIFT_LOCAL_LOGIN_ENABLED=true`` env so production builds without
a configured IdP fail closed instead of silently exposing local auth.
"""

from __future__ import annotations

import os

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.cache import never_cache

# Map IdP kind → the auth1 path the UI should redirect to. The
# canonical /login URL stays the entry point and dispatches on the
# active IdP at request time; this is just a hint for the UI's CTA.
_LOGIN_PATH_BY_KIND = {
    "oidc": "/app/auth1/login",
    "saml": "/app/auth1/login",
    "cognito": "/app/auth1/login",
    "auth0": "/app/auth1/login",
    "okta": "/app/auth1/login",
    "azure_ad": "/app/auth1/login",
    "google": "/app/auth1/login",
    "github": "/app/auth1/login",
    "local": "/app/auth1/local-login",
}


def _local_enabled() -> bool:
    if settings.DEBUG:
        return True
    return os.environ.get("ASTROLIFT_LOCAL_LOGIN_ENABLED", "").lower() in {"1", "true", "yes"}


@never_cache
def active_idp(request):
    from astrolift_identity.models import IdentityProvider, Organization

    # The first organization on the install is the implicit "active org"
    # for unauthenticated callers. (Single-tenant per install — see
    # specs/03 §3.1.) Multi-org installs would need a per-host
    # disambiguation here.
    org = Organization.objects.order_by("created_at").first()
    if org is None:
        return _fallback_local()

    idp = (
        IdentityProvider.objects.filter(pk=org.identity_provider_id).first()
        if org.identity_provider_id
        else None
    )
    if idp is None:
        return _fallback_local()

    return JsonResponse(
        {
            "kind": idp.kind,
            "displayName": idp.display_name or idp.kind,
            "loginPath": _LOGIN_PATH_BY_KIND.get(idp.kind, "/app/auth1/login"),
            "ready": True,
        }
    )


def _fallback_local():
    if not _local_enabled():
        return JsonResponse(
            {
                "kind": None,
                "displayName": "No identity provider configured",
                "loginPath": None,
                "ready": False,
                "hint": (
                    "An operator must configure an IdP via "
                    "/app/gql/config/ → createIdentityProvider before users "
                    "can log in. Local accounts can be enabled by setting "
                    "ASTROLIFT_LOCAL_LOGIN_ENABLED=true."
                ),
            },
            status=200,
        )
    return JsonResponse(
        {
            "kind": "local",
            "displayName": "Username + password",
            "loginPath": "/app/auth1/local-login",
            "ready": True,
        }
    )
