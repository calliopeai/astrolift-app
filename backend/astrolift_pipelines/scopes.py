"""Scope resolvers for pipeline-keyed permission gates (#1731).

A pipeline is an org-level object with an *optional* app association. A
gate that names one checks against that app when the association exists,
and otherwise resolves to nothing, leaving the org check standing -- an
unassociated pipeline has no narrower scope to check against.
"""

from __future__ import annotations

from typing import Any

from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind
from core.scope_args import read_guid
from core.tenancy import get_current_tenant


def _org_id() -> int | None:
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def pipeline_creation_scope(_args) -> PermissionScope:
    """Creation has no app association in its input and creates an org object."""
    from astrolift_identity.api_tokens import get_current_api_token

    org_id = _org_id()
    scope = PermissionScope(kind=ScopeKind.ORG, id=org_id or 0)
    token = get_current_api_token()
    if token is not None and (token.organization_id != org_id or token.team_id is not None):
        raise PermissionDenied(
            Permission.APP_UPDATE, scope, "bearer token does not cover organization pipelines"
        )
    return scope


def pipeline_app_scope(field: str = "id"):
    """Scope on the app a pipeline is associated with, if any."""

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        guid = read_guid(args, field)
        org_id = _org_id()
        if not guid or org_id is None:
            return PermissionScope(kind=ScopeKind.ORG, id=org_id or 0)
        from astrolift_pipelines.models import Pipeline

        app_id = (
            Pipeline.objects.filter(guid=guid, organization_id=org_id)
            .values_list("registered_app__id", flat=True)
            .first()
        )
        return (
            PermissionScope(kind=ScopeKind.APP, id=app_id)
            if app_id
            else PermissionScope(kind=ScopeKind.ORG, id=org_id)
        )

    return _scope


def pipeline_run_app_scope(field: str = "run_id"):
    """Scope on the app the pipeline behind a run is associated with."""

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        guid = read_guid(args, field)
        org_id = _org_id()
        if not guid or org_id is None:
            return PermissionScope(kind=ScopeKind.ORG, id=org_id or 0)
        from astrolift_pipelines.models import PipelineRun

        app_id = (
            PipelineRun.objects.filter(guid=guid, pipeline__organization_id=org_id)
            .values_list("pipeline__registered_app__id", flat=True)
            .first()
        )
        return (
            PermissionScope(kind=ScopeKind.APP, id=app_id)
            if app_id
            else PermissionScope(kind=ScopeKind.ORG, id=org_id)
        )

    return _scope
