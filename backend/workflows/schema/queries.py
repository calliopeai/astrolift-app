from __future__ import annotations

from typing import Optional

import strawberry
from strawberry.types import Info

from workflows.models import WorkflowDefinition, WorkflowInstance, WorkflowStage, WorkflowStageExecution
from workflows.schema.types import (
    WorkflowDefinitionType,
    WorkflowInstanceType,
    WorkflowStageExecutionType,
    WorkflowStageType,
)


@strawberry.type
class Query:

    @strawberry.field(
        description="List workflow definitions visible to the caller: their org's UNION all platform-global (spec 40 §2.1).",
    )
    def workflow_definitions(
        self, info: Info, model_label: Optional[str] = None, include_disabled: bool = True,
    ) -> list[WorkflowDefinitionType]:
        user = info.context.user
        if getattr(user, "is_superuser", False):
            qs = WorkflowDefinition.objects.all()
        else:
            from core.tenancy import get_current_tenant

            tenant = get_current_tenant()
            org_pk = tenant.organization_id if tenant else None
            qs = WorkflowDefinition.visible_to_org(org_pk)
        if not include_disabled:
            qs = qs.filter(is_enabled=True)
        if model_label:
            qs = qs.filter(model_label=model_label)
        return qs

    @strawberry.field(description="Get a visible workflow definition by slug (prefers the caller's org over a global).")
    def workflow_definition(self, info: Info, slug: str) -> Optional[WorkflowDefinitionType]:
        user = info.context.user
        if getattr(user, "is_superuser", False):
            return WorkflowDefinition.objects.filter(slug=slug).first()
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        visible = WorkflowDefinition.visible_to_org(org_pk).filter(slug=slug)
        # Prefer an org-owned definition over a global with the same slug.
        return visible.filter(organization_id=org_pk).first() or visible.first()

    @strawberry.field(description="Get a workflow instance by ID.")
    def workflow_instance(self, info: Info, id: strawberry.ID) -> Optional[WorkflowInstanceType]:
        return WorkflowInstance.objects.filter(pk=id).first()

    @strawberry.field(description="List workflow instances for a specific object.")
    def workflow_instances(
        self, info: Info, object_id: int, model_label: Optional[str] = None,
    ) -> list[WorkflowInstanceType]:
        qs = WorkflowInstance.objects.filter(object_id=object_id)
        if model_label:
            from django.contrib.contenttypes.models import ContentType
            ct = ContentType.objects.filter(
                app_label=model_label.split('.')[0],
                model=model_label.split('.')[-1].lower(),
            ).first()
            if ct:
                qs = qs.filter(content_type=ct)
        return qs

    @strawberry.field(description="List stages for a workflow definition by slug.")
    def workflow_stages(
        self, info: Info, workflow_slug: str,
    ) -> list[WorkflowStageType]:
        workflow = WorkflowDefinition.objects.filter(slug=workflow_slug).first()
        if not workflow:
            return []
        return WorkflowStage.objects.filter(definition=workflow).select_related("agent_definition").order_by("order")

    @strawberry.field(description="List stage executions for a WorkflowRun (by workflow_id + run_id).")
    def workflow_stage_executions(
        self,
        info: Info,
        workflow_id: str,
        run_id: str,
    ) -> list[WorkflowStageExecutionType]:
        from astrolift_operations.models import WorkflowRun
        run = WorkflowRun.objects.filter(workflow_id=workflow_id, run_id=run_id).first()
        if not run:
            return []
        return (
            WorkflowStageExecution.objects.filter(workflow_run=run)
            .select_related("stage", "agent_run")
            .order_by("stage__order", "-created_at")
        )
