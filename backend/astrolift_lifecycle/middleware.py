"""DeployToken bearer middleware (#425).

Companion of :class:`astrolift_identity.middleware.ApiTokenAuthMiddleware`
(#428). Sits in the request chain so any incoming request that
presents an ``Authorization: Bearer alft_dt_…`` header is verified
against the live ``DeployToken`` table and, on success, has its
forensic columns (``last_used_at`` / ``last_used_ip`` /
``last_used_agent``) stamped. The verified row is attached to
``request._deploy_token`` so downstream views (CLI deploy endpoint,
webhook receivers) can read it without re-doing the lookup.

Why a middleware (and not a DRF auth class): deploy tokens are
presented at REST endpoints (CLI deploy POST) and may in future be
accepted at GraphQL — a request-scoped middleware applies uniformly
across both surfaces and gives us a single place to enforce the
forensic touch on every successful auth. DRF auth classes would
only fire on DRF views.

Unlike the API-token middleware, this middleware does **not** swap
``request.user``: deploy tokens are app-scoped credentials with their
own permission model (the deploy endpoint reads
``request._deploy_token.registered_app`` + ``scopes`` directly). The
session-cookie or API-token-resolved user remains attached so any
view that also runs through the standard auth chain still sees the
operator identity.
"""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, JsonResponse

from astrolift_lifecycle.deploy_tokens import (
    PLAINTEXT_PREFIX,
    client_ip_from_request,
    touch_deploy_token,
    user_agent_from_request,
    verify_token,
)


class DeployTokenAuthMiddleware:
    """Authenticate deploy-token bearer requests.

    Honours four exit paths:

    * No ``Authorization: Bearer …`` header → noop, request flows on
      with whatever the session / api-token middleware already
      resolved.
    * Header present but token prefix isn't ``alft_dt_`` → noop;
      another scheme (api token, OIDC, SCIM) will handle it.
    * Prefix matches but verification fails (unknown / revoked /
      expired / outside rotation grace) → return ``401`` so the
      caller doesn't accidentally hit the next middleware with a
      stale request state.
    * Verification succeeds → attach the row to
      ``request._deploy_token`` and stamp the forensic columns.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        bearer = _bearer_from_request(request)
        if bearer and bearer.startswith(PLAINTEXT_PREFIX):
            token = verify_token(bearer)
            if token is None:
                return _unauthorized()

            request._deploy_token = token

            touch_deploy_token(
                token,
                ip=client_ip_from_request(request),
                user_agent=user_agent_from_request(request),
            )

        return self.get_response(request)


def _bearer_from_request(request: HttpRequest) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth or not auth.lower().startswith("bearer "):
        return ""
    return auth[7:].strip()


def _unauthorized() -> HttpResponse:
    return JsonResponse(
        {"detail": "invalid or expired deploy token"},
        status=401,
    )


def get_request_deploy_token(request: HttpRequest):
    """Return the ``DeployToken`` row that authed this request, or None.

    Views call this when they need to scope the operation to the
    token's registered app — e.g. the CLI deploy endpoint refuses a
    deploy against ``app_slug=X`` if the bearer's
    ``registered_app.slug != X``. Returns ``None`` for requests
    authed via cookie / API token / OIDC — those paths don't carry
    an app binding and rely on the role-based permission catalog.
    """
    return getattr(request, "_deploy_token", None)


__all__ = [
    "DeployTokenAuthMiddleware",
    "get_request_deploy_token",
]
