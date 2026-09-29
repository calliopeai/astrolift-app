"""
Tenant-context middleware.

Resolves the request's effective organization/team/project from headers,
session, or token claims, and stashes the result on the contextvar in
``core.tenancy``. The default ORM managers read it from there to scope
queries.

API tokens remain scoped to their issuing organization. A conflicting
``X-Astrolift-Organization`` header is rejected before resolving the tenant.

For session-authenticated requests, resolution order is (first match wins):

1. ``X-Astrolift-Organization`` header (resolved by GUID).
2. ``request.session["organization_id"]`` (set by login flows).
3. The user's *single* active membership (when unambiguous).

Team and project are optional refinements; missing values are treated as
"no scope" and the manager falls back to organization-level filtering.

The middleware never raises on a missing tenant. Resolver-entry
permission checks are responsible for refusing requests that don't have
the scope they need.
"""

from __future__ import annotations

from typing import Any

from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin

from core.tenancy import (
    TenantContext,
    clear_current_tenant,
    set_current_tenant,
)

ORG_HEADER = "HTTP_X_ASTROLIFT_ORGANIZATION"
TEAM_HEADER = "HTTP_X_ASTROLIFT_TEAM"
PROJECT_HEADER = "HTTP_X_ASTROLIFT_PROJECT"


def _resolve_int(meta: dict[str, Any], key: str) -> int | None:
    raw = meta.get(key)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class TenantContextMiddleware(MiddlewareMixin):
    """Populate ``core.tenancy._current`` for the duration of the request."""

    def process_request(self, request):
        from astrolift_identity.api_tokens import token_matches_organization

        # Never inherit a previous request's ABAC attributes or policy cache
        # on a reused worker thread, even on the early-return paths below.
        _clear_request_attributes()

        api_token = getattr(request, "_api_token", None)
        if api_token is not None and not token_matches_organization(
            api_token, request.META.get(ORG_HEADER, "")
        ):
            message = "Selected organization does not match the API token's organization."
            return JsonResponse({"detail": message, "errors": [{"message": message}]}, status=403)

        organization_id = self._resolve_organization_id(request)
        team_id = self._resolve_team_id(request, organization_id)
        project_id = self._resolve_project_id(request, organization_id)
        actor_user_id = (
            getattr(request, "user", None).pk
            if getattr(request, "user", None) is not None and getattr(request.user, "is_authenticated", False)
            else None
        )

        ctx = TenantContext(
            organization_id=organization_id,
            team_id=team_id,
            project_id=project_id,
            actor_user_id=actor_user_id,
        )
        request.astrolift_tenant = ctx
        set_current_tenant(ctx)
        # The request attributes ABAC conditions read (client IP, session
        # age and factors), and the per-request policy cache (#2157).
        from astrolift_identity.abac import attributes_from_request, set_request_attributes

        set_request_attributes(attributes_from_request(request, actor_user_id))

    def process_response(self, request, response):
        clear_current_tenant()
        _clear_request_attributes()
        return response

    def process_exception(self, request, exception):
        clear_current_tenant()
        _clear_request_attributes()
        return None

    # --- resolution helpers ---------------------------------------

    def _resolve_organization_id(self, request) -> int | None:
        # API tokens are minted for exactly one organization. Treat that FK as
        # an authentication boundary: an X-Astrolift-Organization header must
        # never retarget the bearer into a different tenant.
        api_token = getattr(request, "_api_token", None)
        if api_token is not None:
            return api_token.organization_id

        from astrolift_identity.api_tokens import session_may_act_in

        user = getattr(request, "user", None)
        # The header and the session's saved org are caller-chosen: honour
        # them only for an org the user is an active member of (or the
        # platform operator), so a membership SCIM removed stops working
        # at once (#1925). Anything else resolves to no tenant, never to a
        # different org.
        if request.META.get(ORG_HEADER):
            header = self._resolve_header_org(request)
            return header if session_may_act_in(user, header) else None

        session_id = request.session.get("organization_id") if hasattr(request, "session") else None
        if session_id is not None:
            try:
                session_org = int(session_id)
            except (TypeError, ValueError):
                session_org = None
            if session_org is not None:
                return session_org if session_may_act_in(user, session_org) else None

        return _resolve_single_membership_org(request)

    def _resolve_team_id(self, request, organization_id: int | None) -> int | None:
        api_token = getattr(request, "_api_token", None)
        if api_token is not None and api_token.team_id is not None:
            return api_token.team_id
        team_id = _resolve_int(request.META, TEAM_HEADER)
        if team_id is None:
            return None
        # A selected team must belong to the resolved org, for a session as
        # for a token (#1925): no tenant means no team either.
        if organization_id is None:
            return None
        from astrolift_identity.models import Team

        return (
            team_id
            if Team.objects.filter(
                pk=team_id,
                organization_id=organization_id,
                deleted_at__isnull=True,
            ).exists()
            else None
        )

    def _resolve_project_id(self, request, organization_id: int | None) -> int | None:
        project_id = _resolve_int(request.META, PROJECT_HEADER)
        if project_id is None or organization_id is None:
            return None
        api_token = getattr(request, "_api_token", None)
        from astrolift_identity.models import Project

        scope = Project.objects.filter(
            pk=project_id,
            organization_id=organization_id,
            deleted_at__isnull=True,
        )
        if api_token is not None and api_token.team_id is not None:
            scope = scope.filter(team_id=api_token.team_id)
        return project_id if scope.exists() else None

    def _resolve_header_org(self, request) -> int | None:
        raw = request.META.get(ORG_HEADER)
        if not raw:
            return None
        # Header carries the org's external GUID; we look it up to int PK
        # without importing the model at module import time (apps are
        # discovered after settings load).
        from django.apps import apps

        try:
            Organization = apps.get_model("astrolift_identity", "Organization")
        except LookupError:
            return None

        try:
            return Organization.objects.values_list("pk", flat=True).get(guid=raw)
        except Organization.DoesNotExist:
            return None


def _clear_request_attributes() -> None:
    from astrolift_identity.abac import clear_request_attributes

    clear_request_attributes()


def _resolve_single_membership_org(request) -> int | None:
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    from astrolift_identity.api_tokens import active_member_organizations

    org_ids = list(active_member_organizations(user.pk).values_list("pk", flat=True)[:2])
    if len(org_ids) == 1:
        return org_ids[0]
    return None
