"""Durable gate admission and exact recovery against the full Temporal service."""

import asyncio
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client, WorkflowUpdateFailedError
from temporalio.testing import WorkflowEnvironment

from astrolift_identity.models import Organization
from astrolift_operations.models import WorkflowRun
from astrolift_workflows.activities import workflow_stage_activities as activities
from astrolift_workflows.inputs import Actor, WorkflowDefinitionRunInput
from astrolift_workflows.tests.test_serial_collections_temporal_2156 import (
    REGISTERED,
    create_plan,
    replay_family,
)
from astrolift_workflows.workflows.workflow_definition_run import WorkflowDefinitionRunWorkflow
from core.testing.temporal import temporal_worker
from workflows.human_gate_protocol import GATE_UPDATE, decision_update_id
from workflows.models import WorkflowDefinition, WorkflowStage, WorkflowStageExecution

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]


@pytest.fixture
async def gate_engine():
    address = os.getenv("ASTROLIFT_TEST_TEMPORAL_ADDRESS") or os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    client = await Client.connect(address, namespace=os.getenv("TEMPORAL_NAMESPACE", "default"))
    async with WorkflowEnvironment.from_client(client) as env:
        yield env


def gate_plan(kind):
    actor = get_user_model().objects.create_user(username="gate-approver", email="approver@example.test")
    if kind == "root":
        org = Organization.objects.create(name="Gate", slug="gate-update")
        definition = WorkflowDefinition.objects.create(organization=org, name="Gate", slug="gate-update")
        WorkflowStage.objects.create(definition=definition, order=0, kind="human_gate", timeout_seconds=3600)
        run = WorkflowRun.objects.create(
            organization=org,
            workflow_definition=definition,
            workflow_kind="WorkflowDefinitionRunWorkflow",
            workflow_id=f"gate-update-{uuid4()}",
            status="running",
        )
    else:
        plan = create_plan(kind="workflow" if kind == "nested" else "human_gate")
        run = WorkflowRun.objects.get(pk=plan[2])
        definition = run.workflow_definition
        org = run.organization
        if kind == "nested":
            WorkflowStage.objects.filter(definition__slug="serial-body-exact").update(
                kind="human_gate", timeout_seconds=3600, iteration={}
            )
    return org.pk, actor.pk, definition.pk, definition.slug, run.pk, run.workflow_id


async def wait_gate(org_id):
    for _ in range(300):
        row = await sync_to_async(
            lambda: WorkflowStageExecution.objects.filter(
                workflow_run__organization_id=org_id, stage__kind="human_gate", status="running"
            ).first()
        )()
        if row is not None:
            return row
        await asyncio.sleep(0.02)
    pytest.fail("Human gate did not open")


@pytest.mark.parametrize("kind", ["root", "collection", "nested"])
async def test_durable_admission_survives_worker_restart_and_conflicting_signal(gate_engine, kind):
    org, actor, definition, slug, run, workflow_id = await sync_to_async(gate_plan)(kind)
    queue = f"gate-update-{uuid4()}"
    recording_started = asyncio.Event()

    @activity.defn(name="astrolift.workflow_stage.record_human_gate_decision")
    async def pause_recording(
        execution_id: str,
        decision: str,
        user_id: int | None = None,
        note: str = "",
        request: dict | None = None,
    ) -> None:
        recording_started.set()
        await asyncio.Event().wait()

    paused = [pause_recording if fn is activities.record_human_gate_decision else fn for fn in REGISTERED]
    async with temporal_worker(
        gate_engine, task_queue=queue, workflows=[WorkflowDefinitionRunWorkflow], activities=paused
    ):
        root = await gate_engine.client.start_workflow(
            WorkflowDefinitionRunWorkflow.run,
            WorkflowDefinitionRunInput(
                workflow_definition_slug=slug,
                workflow_definition_id=str(definition),
                workflow_run_id=str(run),
                trigger_payload={"items": [{"text": "one"}]},
                actor=Actor(kind="system"),
            ),
            id=workflow_id,
            task_queue=queue,
        )
        row = await wait_gate(org)
        target = gate_engine.client.get_workflow_handle(row.temporal_workflow_id, run_id=row.temporal_run_id)
        for _ in range(300):
            history = await target.fetch_history()
            if any(event.event_type == EventType.EVENT_TYPE_TIMER_STARTED for event in history.events):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("Gate never reached its decision wait")
        payload = {
            "execution_id": str(row.pk),
            "execution_guid": str(row.guid),
            "decision": "approved",
            "decided_by_user_id": actor,
            "note": "Explicit user decision",
        }
        with pytest.raises(WorkflowUpdateFailedError):
            await target.execute_update(
                GATE_UPDATE, payload, id="wrong-id", rpc_timeout=timedelta(seconds=10)
            )
        update_id = decision_update_id(str(row.guid))
        accepted = await target.execute_update(
            GATE_UPDATE, payload, id=update_id, rpc_timeout=timedelta(seconds=10)
        )
        assert accepted == {"state": "requested", **payload}
        await asyncio.wait_for(recording_started.wait(), 10)
        assert (
            await target.execute_update(
                GATE_UPDATE,
                {**payload, "decision": "rejected"},
                id=update_id,
                rpc_timeout=timedelta(seconds=10),
            )
            == accepted
        )
        await target.signal("human_gate_decision", {**payload, "decision": "rejected"})
        await sync_to_async(row.refresh_from_db)()
        assert row.status == "running" and "human_gate" not in (row.output or {})

    async with temporal_worker(
        gate_engine, task_queue=queue, workflows=[WorkflowDefinitionRunWorkflow], activities=REGISTERED
    ):
        result = await asyncio.wait_for(root.result(), 30)
    assert result.ok
    await sync_to_async(row.refresh_from_db)()
    assert row.output["human_gate"] == {
        "decision": "approved",
        "decided_by_user_id": actor,
        "note": "Explicit user decision",
        "request_id": str(row.guid),
    }
    assert row.status == "completed"
    recovered = await target.get_update_handle(update_id).result(rpc_timeout=timedelta(seconds=10))
    assert recovered == accepted
    await replay_family(gate_engine, await root.fetch_history())
