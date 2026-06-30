from __future__ import annotations

from typing import Optional

import strawberry
from strawberry.types import Info

from workflows.models import WorkflowDefinition, WorkflowInstance, WorkflowStage, WorkflowStageExecution
from workflows.schema.types import (
    WorkflowInstanceType,
    WorkflowStageExecutionType,
    WorkflowStageType,
)


@strawberry.type
class Query:
    # ``workflow_definitions`` / ``workflow_definition`` moved to the
    # guardrail-covered, org-scoped ``astrolift_workflows.schema.queries
    # .WorkflowsQuery`` (spec 40 §6, #968).

    @strawberry.field(description="Get a workflow instance by ID.")
    def workflow_instance(self, info: Info, id: strawberry.ID) -> Optional[WorkflowInstanceType]:
        return WorkflowInstance.objects.filter(pk=id).first()

    @strawberry.field(description="List workflow instances for a specific object.")
    def workflow_instances(
        self,
        info: Info,
        object_id: int,
        model_label: Optional[str] = None,
    ) -> list[WorkflowInstanceType]:
        qs = WorkflowInstance.objects.filter(object_id=object_id)
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
        workflow = WorkflowDefinition.objects.filter(slug=workflow_slug).first()
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

        run = WorkflowRun.objects.filter(workflow_id=workflow_id, run_id=run_id).first()
        if not run:
            return []
        return (
            WorkflowStageExecution.objects.filter(workflow_run=run)
            .select_related("stage", "agent_run")
            .order_by("stage__order", "-created_at")
        )
