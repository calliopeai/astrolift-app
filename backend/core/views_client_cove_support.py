"""Authenticated Astrolift proxy for the feature-flagged ClientCove case API."""

from __future__ import annotations

import json

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from config.features import Feature, is_enabled
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind, check_permission
from core.services.client_cove_support import ClientCoveSupportClient
from core.tenancy import get_current_tenant


@require_http_methods(["GET", "POST"])
def support_tickets(request):
    if not is_enabled(Feature.SUPPORT) or not getattr(request.user, "is_authenticated", False):
        return JsonResponse({"error": "support unavailable"}, status=404)
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
        return JsonResponse({"error": "organization unavailable"}, status=403)
    try:
        check_permission(Permission.ORG_READ, scope=PermissionScope(kind=ScopeKind.ORG, id=organization.pk))
    except PermissionDenied:
        return JsonResponse({"error": "support access denied"}, status=403)
    try:
        client = ClientCoveSupportClient(user=request.user, organization=organization)
        if request.method == "GET":
            return JsonResponse(client.list_tickets())
        body = json.loads(request.body or b"{}")
        if not isinstance(body, dict):
            raise ValueError
        return JsonResponse(
            client.create_ticket(
                product=str(body.get("product", "")),
                deployment_id=str(body.get("deployment_id", "")),
                title=str(body.get("title", "")),
                body=str(body.get("body", "")),
            ),
            status=201,
        )
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "invalid support request"}, status=400)
    except RuntimeError:
        return JsonResponse({"error": "support unavailable"}, status=503)
    except Exception:
        return JsonResponse({"error": "support service unavailable"}, status=502)
