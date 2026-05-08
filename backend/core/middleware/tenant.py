"""
Tenant-context middleware.

Resolves the request's effective organization/team/project from headers,
session, or token claims, and stashes the result on the contextvar in
``core.tenancy``. The default ORM managers read it from there to scope
queries.

Resolution order (first match wins):

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

    def process_request(self, request) -> None:
        organization_id = self._resolve_organization_id(request)
        team_id = self._resolve_team_id(request)
        project_id = self._resolve_project_id(request)
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

    def process_response(self, request, response):
        clear_current_tenant()
        return response

    def process_exception(self, request, exception):
        clear_current_tenant()
        return None

    # --- resolution helpers ---------------------------------------

    def _resolve_organization_id(self, request) -> int | None:
        header = self._resolve_header_org(request)
        if header is not None:
            return header

        session_id = request.session.get("organization_id") if hasattr(request, "session") else None
        if session_id is not None:
            try:
                return int(session_id)
            except (TypeError, ValueError):
                pass

        return _resolve_single_membership_org(request)

    def _resolve_team_id(self, request) -> int | None:
        return _resolve_int(request.META, TEAM_HEADER)

    def _resolve_project_id(self, request) -> int | None:
        return _resolve_int(request.META, PROJECT_HEADER)

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


def _resolve_single_membership_org(request) -> int | None:
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    from django.apps import apps

    try:
        Member = apps.get_model("astrolift_identity", "Member")
    except LookupError:
        return None

    org_ids = (
        Member.objects.filter(user_id=user.pk, scope_kind="ORG", is_active=True)
        .values_list("scope_id", flat=True)
        .distinct()
    )
    org_ids = list(org_ids[:2])
    if len(org_ids) == 1:
        return org_ids[0]
    return None
