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


def live_secret_pipelines(qs, *, organization_id: int | None):
    """A secret owner must retain live, coherent ancestors, even outside a request."""
    from django.db.models import F, Q

    from astrolift_registry.models import RegisteredApp

    apps = RegisteredApp.objects.filter(
        organization_id=organization_id,
        organization__deleted_at__isnull=True,
        deleted_at__isnull=True,
    ).filter(
        Q(team_id__isnull=True) | Q(team__organization_id=organization_id, team__deleted_at__isnull=True),
        Q(project_id__isnull=True)
        | Q(
            project__organization_id=organization_id,
            project__deleted_at__isnull=True,
            project__team__organization_id=organization_id,
            project__team__deleted_at__isnull=True,
        ),
        Q(team_id__isnull=True) | Q(project_id__isnull=True) | Q(team_id=F("project__team_id")),
    )
    return qs.filter(
        Q(registered_app_id__isnull=True) | Q(registered_app_id__in=apps.values("pk")),
        organization_id=organization_id,
        organization__deleted_at__isnull=True,
        deleted_at__isnull=True,
    )


def pipeline_secret_scope(field: str = "pipeline_id", *, permissions=(Permission.SECRET_LIST,)):
    """Gate the actual live app, or explicit ORG for an unassociated pipeline.

    A miss never borrows a selected team/project. A bearer must cover this
    organization and the app's owning/shared team, including an admin bearer.
    """

    def _scope(args: dict[str, Any]) -> PermissionScope:
        from astrolift_identity.api_tokens import get_current_api_token
        from astrolift_pipelines.models import Pipeline
        from astrolift_registry.scopes import app_scope_by_guid

        org_id = _org_id()
        guid = read_guid(args, field)
        pipeline = (
            live_secret_pipelines(Pipeline.objects.all(), organization_id=org_id)
            .select_related("registered_app")
            .filter(guid=guid)
            .first()
            if guid
            else None
        )
        if pipeline is not None and pipeline.registered_app_id is not None:
            scope = None
            for permission in permissions:
                scope = app_scope_by_guid(permission=permission)({"app_id": pipeline.registered_app.guid})
            return scope
        scope = PermissionScope(kind=ScopeKind.ORG, id=org_id or 0)
        token = get_current_api_token()
        if token is not None and (token.organization_id != org_id or token.team_id is not None):
            raise PermissionDenied(permissions[0], scope, "credential does not cover organization pipelines")
        return scope

    return _scope


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
