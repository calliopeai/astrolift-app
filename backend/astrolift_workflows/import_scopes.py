"""Authorize workflow imports at the destination their existing API creates."""

from astrolift_identity.api_tokens import get_current_api_token
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind
from core.scope_args import read_arg
from core.tenancy import get_current_tenant
from workflows.scopes import org_scope


def _org_id():
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def _credential_scope(scope, *, team_id=None, permission=Permission.WORKFLOW_CREATE):
    token = get_current_api_token()
    if token is not None and (
        token.organization_id != _org_id() or (token.team_id is not None and token.team_id != team_id)
    ):
        raise PermissionDenied(permission, scope, "bearer token does not cover workflow import owner")
    return scope


def workflow_import_org_scope(_args):
    return _credential_scope(org_scope(_org_id()))


def import_definition_owner_scope(definition, *, permission=Permission.WORKFLOW_CREATE):
    org_id = _org_id()
    project = definition.project if definition is not None else None
    if (
        project is not None
        and project.organization_id == org_id
        and project.deleted_at is None
        and project.team.organization_id == org_id
        and project.team.deleted_at is None
    ):
        return _credential_scope(
            PermissionScope(kind=ScopeKind.PROJECT, id=project.pk),
            team_id=project.team_id,
            permission=permission,
        )
    return _credential_scope(org_scope(org_id), permission=permission)


def resolve_import_project(project_id):
    from django.db import connection

    from astrolift_identity.models import Project

    if project_id is None:
        return None
    projects = Project.objects.filter(
        guid=project_id,
        organization_id=_org_id(),
        deleted_at__isnull=True,
        team__organization_id=_org_id(),
        team__deleted_at__isnull=True,
    ).select_related("team")
    if connection.in_atomic_block:
        projects = projects.select_for_update(of=("self", "team"))
    project = projects.first()
    if project is None:
        raise PermissionDenied(
            Permission.WORKFLOW_CREATE, org_scope(_org_id()), "no live project at this destination"
        )
    return project


def manifest_destination_scope(existing, project_id=None):
    project = resolve_import_project(project_id)
    if existing is not None:
        if project is not None and existing.project_id != project.pk:
            raise PermissionDenied(
                Permission.WORKFLOW_CREATE,
                org_scope(_org_id()),
                "manifest replacement cannot transfer workflow ownership",
            )
        return import_definition_owner_scope(existing)
    if project is not None:
        return _credential_scope(
            PermissionScope(kind=ScopeKind.PROJECT, id=project.pk), team_id=project.team_id
        )
    return workflow_import_org_scope({})


def workflow_manifest_import_scope(args):
    from astrolift_manifest.parser import ManifestError
    from workflows.manifest import parse_workflow_manifest
    from workflows.models import WorkflowDefinition

    project_id = read_arg(args, "project_id")
    if read_arg(args, "preview") is not False or not read_arg(args, "replace"):
        return manifest_destination_scope(None, project_id)
    try:
        parsed = parse_workflow_manifest(read_arg(args, "toml") or "")
    except ManifestError:
        return manifest_destination_scope(None, project_id)
    existing = (
        WorkflowDefinition.objects.filter(
            organization_id=_org_id(), slug=parsed.definition.slug, deleted_at__isnull=True
        )
        .select_related("project__team")
        .first()
    )
    return manifest_destination_scope(existing, project_id)
