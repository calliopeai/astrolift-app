"""A sealed root/child gate executes against PostgreSQL and real Temporal."""

import asyncio
import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from constance.test import override_config
from temporalio.api.enums.v1 import EventType

from astrolift_identity.models import Member
from astrolift_operations.models import WorkflowRun
from astrolift_workflows.activities.configured_workflow_schedule_fire import (
    _create_sync,
    _record_start_sync,
)
from astrolift_workflows.tests.test_serial_collections_temporal_2156 import (
    REGISTERED,
    replay_family,
)
from astrolift_workflows.workflows.workflow_definition_run import WorkflowDefinitionRunWorkflow
from config.schema import schema
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.testing.temporal import temporal_worker
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user
from workflows import atomic_review
from workflows.models import (
    ReviewedWorkflowScheduleExecution,
    Workflow,
    WorkflowDefinition,
    WorkflowStage,
    WorkflowStageExecution,
)
from workflows.tests.test_atomic_review_2459 import browser_info, consent

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]


def reserve_then_edit():
    world = ScopeWorld(f"sealed-{uuid4().hex[:8]}")
    original = make_user(f"original-{uuid4().hex}")
    replacement = make_user(f"replacement-{uuid4().hex}")
    for user in (original, replacement):
        Member.objects.create(user=user, scope_kind="ORG", scope_id=world.org.pk)
        bind_role(
            user,
            permissions=[Permission.WORKFLOW_READ, Permission.WORKFLOW_UPDATE, Permission.WORKFLOW_TRIGGER],
            kind="PROJECT",
            scope_id=world.medops_project.pk,
            slug=f"sealed-{uuid4().hex}",
        )
    root, child = (
        WorkflowDefinition.objects.create(
            organization=world.org,
            project=world.medops_project,
            slug=f"sealed-{label}",
            name=f"Sealed {label}",
            model_label="",
            is_enabled=True,
        )
        for label in ("root", "child")
    )
    nested = WorkflowStage.objects.create(
        definition=root, order=0, slug="nested", kind="workflow", workflow_ref=f"guid:{child.guid}"
    )
    gate = WorkflowStage.objects.create(
        definition=child,
        order=0,
        slug="gate",
        kind="human_gate",
        role="Original reviewer",
        approvers=[original.email],
        timeout_seconds=60,
    )
    checkpoints = [
        WorkflowStage.objects.create(definition=definition, order=1, slug="checkpoint", kind="checkpoint")
        for definition in (root, child)
    ]
    workflow = Workflow.objects.create(
        organization=world.org,
        definition=root,
        created_by=original,
        slug="sealed-configured",
        name="Sealed configured workflow",
        is_enabled=False,
        trigger_kind="schedule",
        schedule_cron="0 * * * *",
        stage_bindings={"0": {"params": {"output_key": "sealed_nested"}}},
    )
    tenant = TenantContext(organization_id=world.org.pk, actor_user_id=original.pk)
    consent_world = SimpleNamespace(w=world, user=original, workflow=workflow)
    with tenant_context(tenant), override_config(REQUIRE_STEP_UP_AUTH=True):
        review = atomic_review.capture_review(workflow_id=str(workflow.guid))
        info = browser_info(consent_world, elevated=True)
        info.context.request.META = {}
        accepted = consent(consent_world, review, info)
    assert accepted.errors is None, accepted.errors
    assert accepted.data["activateReviewedWorkflowSchedule"]["ok"], accepted.data
    fire = _create_sync({"workflow_guid": str(workflow.guid), "organization_id": world.org.pk})
    assert not fire.skipped, fire.skipped
    assert fire.run_input.actor.kind == "system" and fire.run_input.actor.user_id is None
    link = ReviewedWorkflowScheduleExecution.objects.get(workflow_run_id=fire.run_input.workflow_run_id)
    assert link.approval.approver_id == original.pk

    # Edit only synthetic DB content, without calling schedule reconciliation.
    WorkflowStage.objects.filter(pk=nested.pk).update(kind="checkpoint", workflow_ref="")
    WorkflowStage.objects.filter(pk=gate.pk).update(
        kind="checkpoint", role="Replacement reviewer", approvers=[replacement.email]
    )
    WorkflowStage.objects.filter(pk__in=[row.pk for row in checkpoints]).update(kind="human_gate")
    Workflow.objects.filter(pk=workflow.pk).update(
        stage_bindings={"0": {"params": {"output_key": "changed_live_binding"}}}
    )
    return SimpleNamespace(
        fire=fire,
        original=original,
        replacement=replacement,
        org=world.org,
        gate=gate,
        checkpoints=checkpoints,
    )


def waiting_gate(plan):
    return (
        WorkflowStageExecution.objects.select_related("workflow_run", "stage")
        .filter(stage=plan.gate, status="running")
        .first()
    )


def person_signal(plan, execution, user):
    with tenant_context(TenantContext(organization_id=plan.org.pk, actor_user_id=user.pk)):
        info = make_info(user)
        info.context.request.META = {}
        return schema.execute_sync(
            """mutation($workflow: String!, $payload: JSON!) {
              signalWorkflowInstance(workflowId: $workflow,
                signalName: "human_gate_decision", payload: $payload) {
                ok errors { messages }
              }
            }""",
            variable_values={
                "workflow": execution.workflow_run.workflow_id,
                "payload": {
                    "execution_id": str(execution.guid),
                    "decision": "approved",
                    "decided_by_user_id": plan.replacement.pk,
                },
            },
            context_value=info.context,
        )


async def test_reserved_review_runs_sealed_nested_gate_and_native_person_signal(temporal_env, settings):
    # Native transport configuration disables outbound email; no activity,
    # permission, Temporal client, or database implementation is replaced.
    settings.EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"
    settings.ASTROLIFT_SLACK_WEBHOOK_URL = ""
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    plan = await sync_to_async(reserve_then_edit)()
    queue = f"sealed-review-2459-{uuid4().hex}"
    async with temporal_worker(
        temporal_env, task_queue=queue, workflows=[WorkflowDefinitionRunWorkflow], activities=REGISTERED
    ):
        handle = await temporal_env.client.start_workflow(
            WorkflowDefinitionRunWorkflow.run,
            plan.fire.run_input,
            id=plan.fire.workflow_id,
            task_queue=queue,
        )
        finished = False
        try:
            await sync_to_async(_record_start_sync)(
                plan.fire.run_input.workflow_run_id, handle.first_execution_run_id
            )
            async with asyncio.timeout(30):
                while (execution := await sync_to_async(waiting_gate)(plan)) is None:
                    await asyncio.sleep(0.05)
            assert execution.stage.kind == "checkpoint"
            assert execution.effective_stage.kind == "human_gate"
            assert execution.effective_stage.role == "Original reviewer"
            assert execution.effective_stage.approvers == [plan.original.email]
            denied = await sync_to_async(person_signal)(plan, execution, plan.replacement)
            assert denied.errors is None, denied.errors
            assert not denied.data["signalWorkflowInstance"]["ok"], denied.data
            assert "named approvers" in denied.data["signalWorkflowInstance"]["errors"][0]["messages"][0]
            child_workflow_id = execution.workflow_run.workflow_id
            child_handle = temporal_env.client.get_workflow_handle(child_workflow_id)
            before = await child_handle.fetch_history()
            assert not any(
                e.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED for e in before.events
            )
            accepted = await sync_to_async(person_signal)(plan, execution, plan.original)
            assert accepted.errors is None, accepted.errors
            assert accepted.data["signalWorkflowInstance"]["ok"], accepted.data
            result = await asyncio.wait_for(handle.result(), 30)
            finished = True
            history = await handle.fetch_history()
            assert result.ok, result
        finally:
            if not finished:
                await handle.cancel()
    await replay_family(temporal_env, history)
    await sync_to_async(execution.refresh_from_db)()
    assert execution.status == "completed"
    assert execution.output["human_gate"]["decided_by_user_id"] == plan.original.pk
    assert await sync_to_async(
        lambda: list(
            WorkflowRun.objects.filter(
                pk__in=[plan.fire.run_input.workflow_run_id, execution.workflow_run_id]
            )
            .order_by("pk")
            .values_list("status", flat=True)
        )
    )() == ["completed", "completed"]
    rows = await sync_to_async(list)(
        WorkflowStageExecution.objects.filter(stage_id__in=[row.pk for row in plan.checkpoints])
        .select_related("stage")
        .order_by("pk")
    )
    assert len(rows) == 2
    assert all(row.status == "completed" and row.effective_stage.kind == "checkpoint" for row in rows)
    assert all(row.stage.kind == "human_gate" for row in rows)
    assert "sealed_nested" in result.data["named_outputs"]
    assert "changed_live_binding" not in result.data["named_outputs"]

    logging.getLogger(__name__).info(
        "Sealed review native proof completed: root=%s child=%s; original person decided; root/child histories replayed",
        plan.fire.workflow_id,
        child_workflow_id,
    )
