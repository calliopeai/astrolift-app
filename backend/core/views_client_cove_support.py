"""Authenticated Astrolift proxy for the feature-flagged ClientCove case API."""

from __future__ import annotations

import json

import requests
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from config.features import Feature, is_enabled
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind, check_permission
from core.services.client_cove_support import ClientCoveSupportClient
from core.tenancy import get_current_tenant

# Upstream refusals the person can act on, passed through with their own code.
_UPSTREAM = {
    400: ("SUPPORT_REQUEST_INVALID", "invalid support request"),
    402: ("SUPPORT_COVERAGE_REQUIRED", "this deployment has no active support coverage"),
    404: ("NOT_FOUND", "ticket unavailable"),
}


def module_enabled() -> bool:
    """Both the build-time feature and the install's runtime switch (#1809)."""
    from constance import config as constance_config

    try:
        return is_enabled(Feature.SUPPORT) and bool(constance_config.SUPPORT_ENABLED)
    except Exception:
        return False


def _admit(request):
    """The caller's organization, or the refusal to return instead."""
    if not module_enabled() or not getattr(request.user, "is_authenticated", False):
        return None, JsonResponse({"error": "support unavailable"}, status=404)
    from astrolift_identity.api_tokens import SCOPE_ADMIN, get_current_api_token, has_scope
    from astrolift_identity.models import Organization

    tenant = get_current_tenant()
    organization = Organization.objects.filter(pk=tenant.organization_id).first() if tenant else None
    token = get_current_api_token()
    if (
        not request.user.is_active
        or organization is None
        or (
            token is not None
            and (
                token.organization_id != organization.pk
                or token.team_id is not None
                or (request.method == "POST" and not has_scope(token, SCOPE_ADMIN))
            )
        )
    ):
        return None, JsonResponse(
            {"code": "PERMISSION_DENIED", "error": "organization unavailable"}, status=403
        )
    try:
        check_permission(Permission.ORG_READ, scope=PermissionScope(kind=ScopeKind.ORG, id=organization.pk))
    except PermissionDenied:
        return None, JsonResponse({"code": "PERMISSION_DENIED", "error": "support access denied"}, status=403)
    return organization, None


def _body(request) -> dict:
    body = json.loads(request.body or b"{}")
    if not isinstance(body, dict):
        raise ValueError
    return body


def _call(fn, *, created=False):
    try:
        return JsonResponse(fn(), status=201 if created else 200)
    except (ValueError, json.JSONDecodeError):
        return JsonResponse(
            {"code": "SUPPORT_REQUEST_INVALID", "error": "invalid support request"}, status=400
        )
    except RuntimeError:
        return JsonResponse({"code": "SUPPORT_UNAVAILABLE", "error": "support unavailable"}, status=503)
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        if status in _UPSTREAM:
            code, message = _UPSTREAM[status]
            return JsonResponse({"code": code, "error": message}, status=status)
        return JsonResponse(
            {"code": "SUPPORT_UNAVAILABLE", "error": "support service unavailable"}, status=502
        )
    except Exception:
        return JsonResponse(
            {"code": "SUPPORT_UNAVAILABLE", "error": "support service unavailable"}, status=502
        )


@require_http_methods(["GET", "POST"])
def support_tickets(request):
    organization, refused = _admit(request)
    if refused:
        return refused

    def run():
        client = ClientCoveSupportClient(user=request.user, organization=organization)
        if request.method == "GET":
            return client.list_tickets()
        body = _body(request)
        severity = body.get("severity", 3)
        if not isinstance(severity, int) or isinstance(severity, bool) or severity not in (1, 2, 3, 4):
            raise ValueError
        # The deployment and product are this install's, never the browser's.
        deployment_id = getattr(settings, "CLIENT_COVE_DEPLOYMENT_ID", "")
        if not deployment_id:
            raise RuntimeError("CLIENT_COVE_DEPLOYMENT_ID is not configured")
        return client.create_ticket(
            product="astrolift",
            deployment_id=deployment_id,
            title=str(body.get("title", "")),
            body=str(body.get("body", "")),
            severity=severity,
        )

    return _call(run, created=request.method == "POST")


@require_http_methods(["GET", "POST"])
def support_ticket(request, ticket_id):
    organization, refused = _admit(request)
    if refused:
        return refused

    def run():
        client = ClientCoveSupportClient(user=request.user, organization=organization)
        if request.method == "GET":
            return client.get_ticket(str(ticket_id))
        message = str(_body(request).get("message", "")).strip()
        if not message:
            raise ValueError
        return client.comment_ticket(str(ticket_id), message)

    return _call(run)
