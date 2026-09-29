from __future__ import annotations

import strawberry
from django.db.models import Q
from strawberry.types import Info

from astrolift_identity.operation_context import instance_operation, workflow_id_operation
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant
from workflows.models import WorkflowDefinition, WorkflowInstance, WorkflowStage, WorkflowStageExecution
from workflows.schema.types import (
    WorkflowInstanceType,
    WorkflowStageExecutionType,
    WorkflowStageType,
)
from workflows.scopes import (
    definition_scope_by_slug,
    instance_scope_by_id,
    visible_project_owned,
    workflow_run_scope_by_id,
)


def _caller_org_pk():
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def _org_scope_q(org_pk) -> Q:
    """Read scope for the workflows domain (spec 40 §2.1): rows owned by the
    caller's org UNION platform-global (null-org) rows."""
    return Q(organization_id=org_pk) | Q(organization__isnull=True)


def _visible_definition(slug):
    """Resolve *slug* within the caller's read scope — org-owned ∪ platform-
    global, preferring the org-owned row on slug collision. Mirrors
    ``_definition_for_write`` (workflows/schema/mutations.py): a foreign
    org's slug resolves to nothing, byte-identical to nonexistent, so no
    cross-tenant existence oracle."""
    org_pk = _caller_org_pk()
    qs = WorkflowDefinition.objects.filter(slug=slug, deleted_at__isnull=True).filter(_org_scope_q(org_pk))
    return qs.filter(organization_id=org_pk).first() or qs.first()


@strawberry.type
class Query:
    # ``workflow_definitions`` / ``workflow_definition`` moved to the
    # guardrail-covered, org-scoped ``astrolift_workflows.schema.queries
    # .WorkflowsQuery`` (spec 40 §6, #968).

    @strawberry.field(description="Get a workflow instance by ID.")
    @require_permission(
        Permission.WORKFLOW_READ, scope=instance_scope_by_id("id"), operation=instance_operation("id")
    )
    @tenant_scoped()
    def workflow_instance(self, info: Info, id: strawberry.ID) -> WorkflowInstanceType | None:
        # The caller's own org only: a foreign org's instance, and a legacy
        # org-less one, resolve to nothing. Unlike a definition, an instance
        # is not shared platform content (#1965).
        return WorkflowInstance.objects.filter(pk=id, organization_id=_caller_org_pk()).first()

    @strawberry.field(description="List workflow instances for a specific object.")
    @require_permission(Permission.WORKFLOW_READ, any_scope=True)
    @tenant_scoped()
    def workflow_instances(
        self,
        info: Info,
        object_id: int,
        model_label: str | None = None,
    ) -> list[WorkflowInstanceType]:
        org_pk = _caller_org_pk()
        qs = WorkflowInstance.objects.filter(object_id=object_id, organization_id=org_pk)
        qs = visible_project_owned(qs, org_pk, Permission.WORKFLOW_READ, "workflow__project")
        if model_label:
            from django.contrib.contenttypes.models import ContentType

            ct = ContentType.objects.filter(
                app_label=model_label.split(".")[0],
                model=model_label.split(".")[-1].lower(),
            ).first()
            if ct:
                qs = qs.filter(content_type=ct)
        return qs

    @strawberry.field(description="List stages for a workflow definition by slug.")
    @require_permission(Permission.WORKFLOW_READ, scope=definition_scope_by_slug("workflow_slug"))
    @tenant_scoped()
    def workflow_stages(
        self,
        info: Info,
        workflow_slug: str,
    ) -> list[WorkflowStageType]:
        # Org-scoped (#968 follow-up): stages carry prompt/approvers/role,
        # so the definition must resolve within the caller's read scope —
        # a foreign org's slug returns [] exactly like a nonexistent one.
        workflow = _visible_definition(workflow_slug)
        if not workflow:
            return []
        return (
            WorkflowStage.objects.filter(definition=workflow)
            .select_related("agent_definition")
            .order_by("order")
        )

    @strawberry.field(description="List stage executions for a WorkflowRun (by workflow_id + run_id).")
    @require_permission(
        Permission.WORKFLOW_READ,
        scope=workflow_run_scope_by_id("workflow_id", run_field="run_id"),
        operation=workflow_id_operation("workflow_id"),
    )
    @tenant_scoped()
    def workflow_stage_executions(
        self,
        info: Info,
        workflow_id: str,
        run_id: str,
    ) -> list[WorkflowStageExecutionType]:
        from astrolift_operations.models import WorkflowRun

        # The caller's own org only: a foreign org's run resolves to nothing.
        # A run is an execution, not a shared template, so the org ∪
        # platform-global read scope of the definition readers does not
        # apply. An org-less run carries its stage roles, declared approvers
        # and gate state (#69) and belongs to no tenant (#1965). With no
        # tenant, fail closed.
        org_pk = _caller_org_pk()
        if org_pk is None:
            return []
        run = WorkflowRun.objects.filter(
            workflow_id=workflow_id, run_id=run_id, organization_id=org_pk
        ).first()
        if not run:
            return []
        return (
            WorkflowStageExecution.objects.filter(workflow_run=run)
            .select_related(
                "stage",
                "agent_run",
                "child_workflow_run",
                "child_workflow_run__workflow_definition",
            )
            .order_by("stage__order", "-created_at")
        )
