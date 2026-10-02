"""Production serial children use the authored GUID through renames and closure."""

import asyncio

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowFailureError

from astrolift_operations.models import WorkflowRun
from astrolift_workflows.tests.test_serial_collections_temporal_2156 import (
    REGISTERED,
    create_plan,
    replay_family,
    rows,
    start,
)
from astrolift_workflows.workflows.workflow_definition_run import WorkflowDefinitionRunWorkflow
from core.testing.temporal import temporal_worker
from workflows.models import WorkflowDefinition, WorkflowStage

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]


def guid_plan(*, erase=False):
    plan = create_plan(kind="workflow")
    body = WorkflowStage.objects.get(pk=plan[5])
    child = WorkflowDefinition.objects.get(organization=body.definition.organization, slug=body.workflow_ref)
    body.workflow_ref = f"guid:{child.guid}"
    body.save()
    old_slug = child.slug
    child.slug = "renamed-exact-body"
    if erase:
        child.deleted_at = timezone.now()
    child.save()
    shadow = WorkflowDefinition.objects.create(
        organization=body.definition.organization, slug=old_slug, name="Replacement", model_label=""
    )
    WorkflowStage.objects.create(definition=shadow, order=0, kind="checkpoint", output_key="wrong")
    return plan, child.pk


async def test_actual_serial_nested_body_guid_survives_rename_and_reusable_slug_replacement(temporal_env):
    plan, child_pk = await sync_to_async(guid_plan)()
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=REGISTERED
    ):
        handle = await start(temporal_env, plan, {"items": [{"text": "first"}, {"text": "second"}]})
        result = await asyncio.wait_for(handle.result(), 30)
        history = await handle.fetch_history()
    assert result.ok
    assert [record["text"] for record in result.data["final_output"]["results"]] == [
        "Child: first",
        "Child: second",
    ]
    children = await sync_to_async(list)(WorkflowRun.objects.filter(parent_run_id=plan[2]).order_by("pk"))
    assert len(children) == 2
    assert all(child.workflow_definition_id == child_pk and child.status == "completed" for child in children)
    await replay_family(temporal_env, history)


async def test_deleted_explicit_child_guid_refuses_before_any_serial_body_is_dispatched(temporal_env):
    plan, _ = await sync_to_async(guid_plan)(erase=True)
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=REGISTERED
    ):
        handle = await start(temporal_env, plan, {"items": [{"text": "first"}]})
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(handle.result(), 30)
        history = await handle.fetch_history()
    assert await rows(plan[2]) == []
    assert await sync_to_async(lambda: WorkflowRun.objects.get(pk=plan[2]).status)() == "failed"
    assert not any(
        event.event_type == EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED
        for event in history.events
    )
    await replay_family(temporal_env, history)
