from __future__ import annotations

from typing import Optional

import strawberry
from django.db.models import Q
from strawberry.types import Info

from core.tenancy import get_current_tenant
from workflows.models import WorkflowDefinition, WorkflowInstance, WorkflowStage, WorkflowStageExecution
from workflows.schema.types import (
    WorkflowInstanceType,
    WorkflowStageExecutionType,
    WorkflowStageType,
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
    def workflow_instance(self, info: Info, id: strawberry.ID) -> Optional[WorkflowInstanceType]:
        # Org-scoped: a foreign org's instance id resolves to nothing,
        # same closure as the definition readers (#968 follow-up).
        return WorkflowInstance.objects.filter(pk=id).filter(_org_scope_q(_caller_org_pk())).first()

    @strawberry.field(description="List workflow instances for a specific object.")
    def workflow_instances(
        self,
        info: Info,
        object_id: int,
        model_label: Optional[str] = None,
    ) -> list[WorkflowInstanceType]:
        qs = WorkflowInstance.objects.filter(object_id=object_id).filter(_org_scope_q(_caller_org_pk()))
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
    def workflow_stage_executions(
        self,
        info: Info,
        workflow_id: str,
        run_id: str,
    ) -> list[WorkflowStageExecutionType]:
        from astrolift_operations.models import WorkflowRun

        # Org-scoped: a foreign org's run resolves to nothing (same
        # closure as workflow_stages above).
        #
        # Fail closed with no tenant. The shared read scope is
        # org ∪ platform-global, which for a null org compiles to
        # "organization_id IS NULL OR organization IS NULL" and would hand
        # back every org-less run in the install — including, since #69, its
        # stage roles, declared approvers, and gate state.
        org_pk = _caller_org_pk()
        if org_pk is None:
            return []
        run = (
            WorkflowRun.objects.filter(workflow_id=workflow_id, run_id=run_id)
            .filter(_org_scope_q(org_pk))
            .first()
        )
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
