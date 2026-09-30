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


def manifest_destination_scope(parsed, existing):
    from workflows.manifest import shape_compatible

    # A replacement with a different shape creates a new ORG version.
    if existing is not None and shape_compatible(existing, parsed):
        return import_definition_owner_scope(existing)
    return workflow_import_org_scope({})


def workflow_manifest_import_scope(args):
    from astrolift_manifest.parser import ManifestError
    from workflows.manifest import parse_workflow_manifest
    from workflows.models import WorkflowDefinition

    if read_arg(args, "preview") is not False or not read_arg(args, "replace"):
        return workflow_import_org_scope(args)
    try:
        parsed = parse_workflow_manifest(read_arg(args, "toml") or "")
    except ManifestError:
        return workflow_import_org_scope(args)
    existing = (
        WorkflowDefinition.objects.filter(
            organization_id=_org_id(), slug=parsed.definition.slug, deleted_at__isnull=True
        )
        .select_related("project__team")
        .first()
    )
    return manifest_destination_scope(parsed, existing)
