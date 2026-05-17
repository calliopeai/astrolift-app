"""ApiToken bearer middleware (#428).

When the incoming request carries an ``Authorization: Bearer
alft_at_…`` header, look up the token, verify it isn't revoked /
expired, stamp ``last_used_at`` / ``last_used_ip`` /
``last_used_agent``, and attach the authenticated user to
``request.user`` so downstream resolvers behave as if the operator
logged in with a session cookie.

Sits *after* ``AuthenticationMiddleware`` in the chain so the user
attribute exists; sits *before* ``CurrentUserMiddleware`` so the
thread-local picks up the token-authed user.

Why a middleware (not a DRF auth class): this project's API surface
is mostly Strawberry GraphQL, not DRF views. DRF auth classes would
not fire on the GraphQL endpoint. A request-scoped middleware
applies uniformly across REST, GraphQL, and webhook paths.

Scope enforcement is deferred to the resolver layer — the middleware
attaches the token to ``request._api_token`` so resolvers can call
:func:`astrolift_identity.api_tokens.enforce_scopes` for the
operation they're about to perform. Centralized here would force us
to map every URL to a required scope; resolver-level keeps the
permission catalog as the single source of truth.
"""

from __future__ import annotations

from collections.abc import Callable

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, HttpResponse, JsonResponse

from astrolift_identity.api_tokens import (
    PLAINTEXT_PREFIX,
    client_ip_from_request,
    touch_token,
    user_agent_from_request,
    verify_token,
)


class ApiTokenAuthMiddleware:
    """Authenticate API-token bearer requests.

    Honours four exit paths:

    * No ``Authorization: Bearer …`` header → noop, request flows on
      with whatever the session middleware already resolved.
    * Header present but token prefix isn't ``alft_at_`` → noop;
      another scheme (deploy token, OIDC) will handle it.
    * Prefix matches but verification fails → return ``401`` so the
      caller doesn't accidentally hit the next middleware with a
      stale ``request.user``.
    * Verification succeeds → swap ``request.user`` to the token's
      owner, attach the row to ``request._api_token``, stamp
      last-used columns.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        bearer = _bearer_from_request(request)
        if bearer and bearer.startswith(PLAINTEXT_PREFIX):
            token = verify_token(bearer)
            if token is None:
                return _unauthorized()

            # ``user_id`` is the integer FK; resolving via the manager
            # gives us a fully populated User instance so downstream
            # ``request.user.is_authenticated`` returns True.
            user_model = get_user_model()
            user = user_model.objects.filter(pk=token.user_id).first()
            if user is None or not user.is_active:
                return _unauthorized()

            request.user = user
            request._api_token = token

            touch_token(
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
        {"detail": "invalid or expired api token"},
        status=401,
    )


def get_request_api_token(request: HttpRequest):
    """Return the ``ApiToken`` row that authed this request, or None.

    Resolvers call this when they need to scope-check the operation:

        token = get_request_api_token(request)
        if token is not None:
            missing = enforce_scopes(token, ("write:apps",))
            if missing:
                return permission_denied(...)

    Resolvers without an attached token (session-cookie auth, dev
    auto-login) get back ``None`` and skip the scope check — session
    auth carries the full role-binding permission set already.
    """
    return getattr(request, "_api_token", None)


# Re-export for downstream call sites; keeps the import surface
# narrow ("from astrolift_identity.middleware import …") instead of
# splitting between middleware and api_tokens.
__all__ = [
    "ApiTokenAuthMiddleware",
    "AnonymousUser",
    "get_request_api_token",
]
