"""Shared exact identity resolution for native configured workflow operations."""

from uuid import UUID

from core.permissions import Permission
from core.scope_args import read_arg
from core.tenancy import get_current_tenant
from workflows.scopes import reviewed_definition_scope


def _target(rows, *, guid, slug, lock=False):
    if guid is not None:
        try:
            identity = UUID(str(guid))
        except (ValueError, TypeError, AttributeError):
            return None
        rows = rows.filter(guid=identity)
        if slug is not None:
            rows = rows.filter(slug=slug)
    elif slug:
        rows = rows.filter(slug=slug)
    else:
        return None
    if lock:
        rows = rows.select_for_update(of=("self",))
    return rows


def find_configuration_definition(org_id, *, definition_id=None, definition_slug=None, lock=False):
    from workflows.models import WorkflowDefinition

    rows = _target(
        WorkflowDefinition.visible_to_org(org_id)
        .filter(deleted_at__isnull=True)
        .select_related("project__team"),
        guid=definition_id,
        slug=definition_slug,
        lock=lock,
    )
    if rows is None:
        return None
    return rows.filter(organization_id=org_id).first() or rows.filter(organization__isnull=True).first()


def find_configured_workflow(org_id, *, workflow_id=None, slug=None, lock=False, include_deleted=False):
    from workflows.models import Workflow

    candidates = Workflow.objects.filter(organization_id=org_id)
    if not include_deleted:
        candidates = candidates.filter(deleted_at__isnull=True)
    rows = _target(
        candidates.select_related("definition__project__team", "organization"),
        guid=workflow_id,
        slug=slug,
        lock=lock,
    )
    return rows.first() if rows is not None else None


def configuration_definition_scope(args):
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    definition = find_configuration_definition(
        org_id,
        definition_id=read_arg(args, "definition_id"),
        definition_slug=read_arg(args, "definition_slug"),
    )
    return reviewed_definition_scope(definition, org_id, permission=Permission.WORKFLOW_CREATE)


def configured_workflow_scope(permission):
    def resolve(args):
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        workflow = find_configured_workflow(
            org_id,
            workflow_id=read_arg(args, "workflow_id"),
            slug=read_arg(args, "slug"),
        )
        return reviewed_definition_scope(
            workflow.definition if workflow is not None else None,
            org_id,
            permission=permission,
        )

    return resolve
