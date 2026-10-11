"""Owned native schedule inspection and reconciliation, including cleanup after deletion."""

from core.permissions import Permission, check_permission
from core.scope_args import read_arg
from core.tenancy import get_current_tenant
from workflows.scopes import reviewed_definition_scope

from .configuration_targets import find_configured_workflow
from .import_scopes import import_definition_owner_scope


def schedule_workflow(workflow_id):
    tenant = get_current_tenant()
    return find_configured_workflow(
        tenant.organization_id if tenant else None,
        workflow_id=workflow_id,
        include_deleted=True,
    )


def workflow_schedule_scope(permission):
    def resolve(args):
        workflow = schedule_workflow(read_arg(args, "workflow_id"))
        return import_definition_owner_scope(workflow.definition if workflow else None, permission=permission)

    return resolve


def authorize_schedule(workflow, permission=Permission.WORKFLOW_UPDATE):
    from workflows.schedule_sync import schedule_inactive_reason

    check_permission(
        permission, scope=import_definition_owner_scope(workflow.definition, permission=permission)
    )
    if workflow.deleted_at is not None and permission != Permission.WORKFLOW_DELETE:
        check_permission(
            Permission.WORKFLOW_DELETE,
            scope=import_definition_owner_scope(workflow.definition, permission=Permission.WORKFLOW_DELETE),
        )
    if schedule_inactive_reason(workflow) is None:
        check_permission(
            Permission.WORKFLOW_TRIGGER,
            scope=reviewed_definition_scope(
                workflow.definition, workflow.organization_id, permission=Permission.WORKFLOW_TRIGGER
            ),
        )


def validate_schedule_configuration(workflow):
    from core.schema.common import ValidationError
    from workflows.schedule_sync import schedule_inactive_reason

    if workflow.is_enabled and workflow.trigger_kind == "schedule":
        if not workflow.schedule_cron:
            return ValidationError(
                field="schedule_cron", messages=["An enabled scheduled workflow requires a cron expression."]
            )
        reason = schedule_inactive_reason(workflow)
        if reason:
            return ValidationError(field="definition_id", messages=[reason])
    return None
