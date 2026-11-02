"""The factory gate token endpoint (#2589): a machine path, not GraphQL.

``POST /api/scm/v1/factory-gate/token/`` with ``Authorization: Bearer
alft_at_...`` answers a short-lived installation token of the bearer's own
organization's factory gate GitHub App, carrying organization Members read and
nothing else. A browser session is refused: no page needs this token. The
rules (the caller allowlist, the permission set, the audit) live in
``astrolift_scm.services.factory_gate.mint_token``.

Body, optional: ``{"permissions": {"members": "read"}}``; any other
permission is refused.
"""

from __future__ import annotations

import json

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from astrolift_scm.services import factory_gate
from core.permissions import route_auth


@require_http_methods(["POST"])
@route_auth(
    credential="api-token",
    scope=(
        "the bearer's own organization's factory gate App; token GUID listed in "
        "FACTORY_GATE_TOKEN_CALLERS; organization Members read only"
    ),
)
def factory_gate_token(request: HttpRequest) -> JsonResponse:
    api_token = getattr(request, "_api_token", None)
    if api_token is None:
        return _no_store(
            JsonResponse(
                {"code": "API_TOKEN_REQUIRED", "detail": "send Authorization: Bearer alft_at_..."},
                status=401,
            )
        )
    try:
        minted = factory_gate.mint_token(api_token, _requested_permissions(request))
    except factory_gate.MintRefused as exc:
        return _no_store(JsonResponse({"code": exc.code, "detail": exc.message}, status=exc.status))
    return _no_store(
        JsonResponse(
            {
                "token": minted.token,
                "expires_at": minted.expires_at.isoformat(),
                "permissions": minted.permissions,
            }
        )
    )


def _requested_permissions(request: HttpRequest) -> object:
    """``permissions`` from a JSON object body; None when the body is empty or names none."""
    if not request.body:
        return None
    try:
        body = json.loads(request.body)
    except ValueError:
        return factory_gate.MALFORMED
    if not isinstance(body, dict):
        return factory_gate.MALFORMED
    return body.get("permissions")


def _no_store(response: JsonResponse) -> JsonResponse:
    response["Cache-Control"] = "no-store"
    return response
