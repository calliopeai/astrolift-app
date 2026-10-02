"""Real PostgreSQL and production Temporal activities, with serial child histories."""

import asyncio
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer, Worker

from astrolift_identity.models import Organization
from astrolift_operations.models import WorkflowRun
from astrolift_workflows.activities import workflow_stage_activities as activities
from astrolift_workflows.inputs import Actor, WorkflowDefinitionRunInput
from astrolift_workflows.schema.mutations import _execution_signal_target
from astrolift_workflows.workflows.workflow_definition_run import (
    WorkflowDefinitionRunWorkflow,
)
from config.schema import schema
from core.tenancy import TenantContext, tenant_context
from core.testing.temporal import temporal_worker
from workflows.importers.langflow import LangflowImporter
from workflows.manifest import (
    create_definition_from_manifest,
    emit_workflow_manifest,
    parse_workflow_manifest,
)
from workflows.models import WorkflowDefinition, WorkflowStage, WorkflowStageExecution
from workflows.tests.test_langflow_collections_2156 import source_collection

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]
REGISTERED = [
    activities.get_workflow_stages,
    activities.create_stage_execution,
    activities.snapshot_checkpoint,
    activities.update_stage_execution,
    activities.mark_workflow_run,
    activities.record_human_gate_decision,
    activities.create_nested_workflow_run,
    activities.record_nested_workflow_start,
]


def create_plan(*, texts=None, kind="format_record", source=False):
    org = Organization.objects.create(name="Serial collection proof", slug="serial-collection-proof")
    if source:
        imported = LangflowImporter().import_flow(source_collection(texts))
        manifest = parse_workflow_manifest(emit_workflow_manifest(imported.manifest))
        definition = create_definition_from_manifest(manifest, organization=org, is_enabled=True)
    else:
        definition = WorkflowDefinition.objects.create(
            organization=org,
            slug="serial-collection-proof",
            name="Serial",
            model_label="",
            pattern_kind="chained",
        )
        WorkflowStage.objects.create(
            definition=definition,
            order=0,
            kind="collection",
            output_key="each",
            iteration={"max_items": 3, "items_path": "items", "body_end": "body"},
        )
        WorkflowStage.objects.create(
            definition=definition,
            order=1,
            kind=kind,
            output_key="body",
            timeout_seconds=3600,
            iteration={
                "source_format": "langflow_parser",
                "pattern": "Item: {text}",
                "separator": "\n",
            }
            if kind == "format_record"
            else {},
        )
    owner, body = definition.stages.order_by("order")
    if kind == "workflow" and not source:
        child = WorkflowDefinition.objects.create(
            organization=org,
            slug="serial-body-exact",
            name="Exact body",
            model_label="",
        )
        WorkflowStage.objects.create(
            definition=child,
            order=0,
            kind="format_record",
            output_key="child_result",
            iteration={
                "source_format": "langflow_parser",
                "pattern": "Child: {text}",
                "separator": "\n",
            },
        )
        body.workflow_ref = child.slug
        body.save()
    run = WorkflowRun.objects.create(
        organization=org,
        workflow_definition=definition,
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id=f"serial-collection-{uuid.uuid4()}",
        run_id="",
        status="running",
    )
    return definition.pk, definition.slug, run.pk, run.workflow_id, owner.pk, body.pk


async def start(env, plan, payload=None):
    definition, slug, run, workflow_id, _, _ = plan
    return await env.client.start_workflow(
        WorkflowDefinitionRunWorkflow.run,
        WorkflowDefinitionRunInput(
            workflow_definition_slug=slug,
            workflow_definition_id=str(definition),
            workflow_run_id=str(run),
            trigger_payload={} if payload is None else payload,
            actor=Actor(kind="system"),
        ),
        id=workflow_id,
        task_queue="astrolift-test",
    )


async def rows(run):
    return await sync_to_async(list)(
        WorkflowStageExecution.objects.filter(workflow_run_id=run).order_by("pk")
    )


async def replay_family(env, history):
    await Replayer(workflows=[WorkflowDefinitionRunWorkflow]).replay_workflow(history)
    for event in history.events:
        if event.event_type == EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_STARTED:
            child = event.child_workflow_execution_started_event_attributes.workflow_execution
            child_history = await env.client.get_workflow_handle(
                child.workflow_id, run_id=child.run_id
            ).fetch_history()
            await replay_family(env, child_history)


@pytest.mark.parametrize("texts", [[], [""], ["third", "first", "second"]])
async def test_real_import_authoring_and_serial_parser_done_preserve_order_and_replay(temporal_env, texts):
    plan = await sync_to_async(create_plan)(texts=texts, source=True)
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=REGISTERED
    ):
        handle = await start(temporal_env, plan)
        result = await asyncio.wait_for(handle.result(), 30)
        history = await handle.fetch_history()
    assert result.ok
    outputs = result.data["final_output"]
    assert outputs["complete"] and outputs["finished_count"] == len(texts)
    assert [record["text"] for record in outputs["results"]] == [f"Item: {text}" for text in texts]
    assert all(
        re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{6} ", record["timestamp"])
        for record in outputs["results"]
    )
    executions = await rows(plan[2])
    assert executions[0].stage_id == plan[4] and executions[0].status == "completed"
    items = executions[1:]
    assert [row.collection_index for row in items] == list(range(len(texts)))
    assert all(
        row.collection_parent_execution_id == executions[0].pk
        and row.fanout_parent_execution_id is None
        and row.status == "completed"
        for row in items
    )
    assert all(left.ended_at <= right.started_at for left, right in zip(items, items[1:], strict=False))
    assert [row.output for row in items] == outputs["results"]
    assert await sync_to_async(lambda: WorkflowRun.objects.get(pk=plan[2]).status)() == "completed"
    await replay_family(temporal_env, history)


async def test_actual_pre_abort_collection_parent_and_item_histories_still_replay():
    folder = Path(__file__).parent / "fixtures" / "serial_collection_pre_abort_2156"
    for name in ("parent", "item"):
        history = WorkflowHistory.from_json("archived-collection", (folder / f"{name}.json").read_text())
        await Replayer(workflows=[WorkflowDefinitionRunWorkflow]).replay_workflow(history)


@pytest.mark.parametrize("payload", [{}, {"items": None}, {"items": [{}] * 4}, {"items": ["bad"]}])
async def test_missing_or_oversize_collection_refuses_before_any_body_or_child(temporal_env, payload):
    plan = await sync_to_async(create_plan)()
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=REGISTERED
    ):
        handle = await start(temporal_env, plan, payload)
        result = await asyncio.wait_for(handle.result(), 30)
        history = await handle.fetch_history()
    assert not result.ok and result.data["status"] == "unavailable"
    assert await rows(plan[2]) == []
    assert not any(
        event.event_type == EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED
        for event in history.events
    )
    await replay_family(temporal_env, history)


async def test_public_input_reserved_fields_cannot_select_body_or_spoof_item_metadata(
    temporal_env,
):
    plan = await sync_to_async(create_plan)()
    payload = {
        "items": [{"text": "authorized input"}],
        "_astrolift_collection": {
            "parent_execution_id": "foreign-parent",
            "body_stage_ids": ["foreign-stage"],
            "round_number": 999,
        },
        "_astrolift_collection_item": {"text": "spoofed input"},
    }
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=REGISTERED
    ):
        handle = await start(temporal_env, plan, payload)
        result = await asyncio.wait_for(handle.result(), 30)
        history = await handle.fetch_history()
    assert result.ok and result.data["final_output"]["results"][0]["text"] == "Item: authorized input"
    executions = await rows(plan[2])
    assert executions[1].round_number == 1 and executions[1].collection_index == 0
    assert executions[1].collection_parent_execution_id == executions[0].pk
    await replay_family(temporal_env, history)


async def test_each_item_executes_the_exact_nested_workflow_binding_serially(
    temporal_env,
):
    plan = await sync_to_async(create_plan)(kind="workflow")
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=REGISTERED
    ):
        handle = await start(temporal_env, plan, {"items": [{"text": "two"}, {"text": "one"}]})
        result = await asyncio.wait_for(handle.result(), 30)
        history = await handle.fetch_history()
    assert result.ok
    assert [record["text"] for record in result.data["final_output"]["results"]] == [
        "Child: two",
        "Child: one",
    ]
    executions = await rows(plan[2])
    children = await sync_to_async(list)(
        WorkflowRun.objects.filter(parent_run_id=plan[2]).select_related("workflow_definition").order_by("pk")
    )
    assert len(children) == 2 and all(
        child.workflow_definition.slug == "serial-body-exact" and child.status == "completed"
        for child in children
    )
    assert all(row.collection_index is not None and row.collection_workflow_id for row in executions[1:])
    await replay_family(temporal_env, history)


async def wait_gate(run_id, index):
    for _ in range(300):
        row = await sync_to_async(
            lambda: WorkflowStageExecution.objects.filter(
                workflow_run_id=run_id, collection_index=index, status="running"
            ).first()
        )()
        if row:
            return row
        await asyncio.sleep(0.02)
    pytest.fail("the real worker did not open the exact serial gate item")


async def test_no_done_or_next_item_before_gate_approval_and_worker_restart(
    temporal_env,
):
    plan = await sync_to_async(create_plan)(kind="human_gate")
    async with Worker(
        temporal_env.client,
        task_queue="astrolift-test",
        workflows=[WorkflowDefinitionRunWorkflow],
        activities=REGISTERED,
        max_cached_workflows=0,
    ):
        handle = await start(temporal_env, plan, {"items": [{"text": "first"}, {"text": "second"}]})
        first = await wait_gate(plan[2], 0)
        org_id = await sync_to_async(lambda: WorkflowRun.objects.get(pk=plan[2]).organization_id)()
        child_id = await sync_to_async(_execution_signal_target)(
            str(first.pk), plan[3], organization_id=org_id
        )
        assert child_id == first.collection_workflow_id and child_id != plan[3]
        child = temporal_env.client.get_workflow_handle(child_id)
        for _ in range(200):
            history = await child.fetch_history()
            if any(event.event_type == EventType.EVENT_TYPE_TIMER_STARTED for event in history.events):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("gate did not durably wait before worker restart")
        assert len(await rows(plan[2])) == 2
        assert await sync_to_async(lambda: WorkflowRun.objects.get(pk=plan[2]).status)() == "running"
    async with Worker(
        temporal_env.client,
        task_queue="astrolift-test",
        workflows=[WorkflowDefinitionRunWorkflow],
        activities=REGISTERED,
        max_cached_workflows=0,
    ):
        await child.signal(
            "human_gate_decision",
            {"execution_id": str(first.pk), "decision": "approved"},
        )
        second = await wait_gate(plan[2], 1)
        assert first.pk != second.pk
        await temporal_env.client.get_workflow_handle(second.collection_workflow_id).signal(
            "human_gate_decision",
            {"execution_id": str(second.pk), "decision": "approved"},
        )
        result = await asyncio.wait_for(handle.result(), 30)
        history = await handle.fetch_history()
    assert result.ok and result.data["final_output"]["finished_count"] == 2
    await replay_family(temporal_env, history)


async def test_failed_first_body_stops_without_done_or_later_item(temporal_env):
    plan = await sync_to_async(create_plan)(kind="human_gate")
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=REGISTERED
    ):
        handle = await start(temporal_env, plan, {"items": [{"text": "first"}, {"text": "never"}]})
        gate = await wait_gate(plan[2], 0)
        await temporal_env.client.get_workflow_handle(gate.collection_workflow_id).signal(
            "human_gate_decision",
            {"execution_id": str(gate.pk), "decision": "rejected"},
        )
        result = await asyncio.wait_for(handle.result(), 30)
        history = await handle.fetch_history()
    assert not result.ok and result.data["status"] == "incomplete" and result.data["finished_count"] == 0
    executions = await rows(plan[2])
    assert len(executions) == 2 and all(row.status == "failed" for row in executions)
    assert executions[0].output["complete"] is False
    await replay_family(temporal_env, history)


async def test_actual_graphql_gate_signal_delivers_to_exact_recorded_item_child(temporal_env, settings):
    from astrolift_workflows import client as temporal_client

    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    settings.TEMPORAL_ADDRESS = temporal_env.client.service_client.config.target_host
    settings.TEMPORAL_NAMESPACE = temporal_env.client.namespace
    previous_client = temporal_client._client
    temporal_client._client = None
    plan = await sync_to_async(create_plan)(kind="human_gate")

    def approve(gate):
        user = get_user_model().objects.create_superuser(
            username="serial-gate-approver", email="gate@example.test", password="test"
        )
        run = WorkflowRun.objects.get(pk=plan[2])
        with tenant_context(TenantContext(organization_id=run.organization_id, actor_user_id=user.pk)):
            reply = schema.execute_sync(
                'mutation($id:String!,$payload:JSON!){signalWorkflowInstance(workflowId:$id,signalName:"human_gate_decision",payload:$payload){ok errors{field messages}}}',
                variable_values={
                    "id": run.workflow_id,
                    "payload": {
                        "execution_id": str(gate.guid),
                        "decision": "approved",
                        "decided_by_user_id": -1,
                    },
                },
                context_value=SimpleNamespace(user=user, request=None),
            )
        return reply, user.pk

    try:
        async with temporal_worker(
            temporal_env,
            workflows=[WorkflowDefinitionRunWorkflow],
            activities=REGISTERED,
        ):
            handle = await start(temporal_env, plan, {"items": [{"text": "one"}]})
            gate = await wait_gate(plan[2], 0)
            reply, actor_id = await sync_to_async(approve)(gate)
            assert not reply.errors and reply.data["signalWorkflowInstance"]["ok"]
            result = await asyncio.wait_for(handle.result(), 30)
            history = await handle.fetch_history()
        assert result.ok
        persisted = await sync_to_async(lambda: WorkflowStageExecution.objects.get(pk=gate.pk).output)()
        assert persisted["human_gate"]["decided_by_user_id"] == actor_id
        await replay_family(temporal_env, history)
    finally:
        temporal_client._client = previous_client


def create_agent_plan(*, attempts=2):
    from astrolift_identity.models import Project, Team
    from astrolift_registry.models import RegisteredApp, Workload

    plan = create_plan(kind="agent_dispatch")
    definition = WorkflowDefinition.objects.get(pk=plan[0])
    org = definition.organization
    team = Team.objects.create(organization=org, name="Exact agent team", slug="exact-agent-team")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Exact agent project",
        slug="exact-agent-project",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Exact agent app",
        slug="exact-agent-app",
        provisioning_status="ready",
    )
    exact = Workload.objects.create(registered_app=app, name="Exact worker", slug="worker", kind="agent")
    sibling_app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Other agent app",
        slug="other-agent-app",
        provisioning_status="ready",
    )
    sibling = Workload.objects.create(
        registered_app=sibling_app, name="Other worker", slug="worker", kind="agent"
    )
    body = WorkflowStage.objects.get(pk=plan[5])
    body.agent_definition = exact
    body.agent_ref = "worker"
    body.on_failure = "retry"
    body.max_attempts = attempts
    body.save()
    return plan, exact.pk, sibling.pk


async def test_real_agent_body_keeps_exact_app_binding_and_bounds_unavailable_target_retries(
    temporal_env,
):
    from astrolift_agents.models import AgentTask
    from astrolift_lifecycle.models import AgentRun

    plan, exact_id, sibling_id = await sync_to_async(create_agent_plan)()
    registered = [
        *REGISTERED,
        activities.dispatch_agent_for_stage,
        activities.poll_agent_run_status,
        activities.poll_agent_run_progress,
        activities.load_agent_run_outcome,
    ]
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=registered
    ):
        handle = await start(
            temporal_env,
            plan,
            {"items": [{"text": "actual first item"}, {"text": "never dispatched"}]},
        )
        result = await asyncio.wait_for(handle.result(), 30)
        history = await handle.fetch_history()
    assert not result.ok
    executions = await rows(plan[2])
    assert [(row.collection_index, row.attempt_number, row.status) for row in executions[1:]] == [
        (0, 1, "failed"),
        (0, 2, "failed"),
    ]
    tasks = await sync_to_async(list)(
        AgentTask.objects.filter(agent_run_id__in=[row.agent_run_id for row in executions[1:]])
        .select_related("agent_run", "brief")
        .order_by("pk")
    )
    assert len(tasks) == 2
    assert all(
        task.agent_definition_id == exact_id and task.agent_run.workload_id == exact_id for task in tasks
    )
    assert all(
        task.status == "failed"
        and task.dispatch_input["text"] == "actual first item"
        and task.brief_id is not None
        for task in tasks
    )
    assert all(
        task.dispatch_input["_astrolift_workflow"]["previous"] == {"text": "actual first item"}
        for task in tasks
    )
    assert all(task.dispatch_input["_astrolift_workflow"]["stage"]["round_number"] == 1 for task in tasks)
    assert all(task.organization_id == task.brief.organization_id for task in tasks)
    assert not await sync_to_async(AgentRun.objects.filter(workload_id=sibling_id).exists)()
    assert executions[0].output["complete"] is False and executions[0].output["finished_count"] == 0
    assert await sync_to_async(lambda: WorkflowRun.objects.get(pk=plan[2]).status)() == "failed"
    await replay_family(temporal_env, history)


def create_review_collection():
    plan = create_plan(kind="checkpoint")
    definition = WorkflowDefinition.objects.get(pk=plan[0])
    owner = WorkflowStage.objects.get(pk=plan[4])
    owner.iteration = {**owner.iteration, "body_end": "review"}
    owner.save()
    WorkflowStage.objects.create(
        definition=definition,
        order=2,
        kind="human_gate",
        output_key="review",
        timeout_seconds=3600,
        back_edge={
            "to": "body",
            "when": "gate_rejected",
            "max_rounds": 2,
            "on_exhausted": "fail",
        },
    )
    return plan


async def test_review_returns_and_round_causes_are_bounded_inside_each_serial_item(
    temporal_env,
):
    plan = await sync_to_async(create_review_collection)()
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=REGISTERED
    ):
        handle = await start(temporal_env, plan, {"items": [{"text": "first"}, {"text": "second"}]})

        async def gate_at(index, round_number):
            for _ in range(300):
                row = await sync_to_async(
                    lambda: WorkflowStageExecution.objects.filter(
                        workflow_run_id=plan[2],
                        collection_index=index,
                        round_number=round_number,
                        stage__kind="human_gate",
                        status="running",
                    ).first()
                )()
                if row:
                    return row
                await asyncio.sleep(0.02)
            pytest.fail("the exact collection review visit did not open")

        first = await gate_at(0, 1)
        await temporal_env.client.get_workflow_handle(first.collection_workflow_id).signal(
            "human_gate_decision",
            {"execution_id": str(first.pk), "decision": "rejected"},
        )
        returned = await gate_at(0, 2)
        assert returned.caused_by["reason"] == "gate_rejected" and returned.caused_by["edge_round"] == 2
        await temporal_env.client.get_workflow_handle(returned.collection_workflow_id).signal(
            "human_gate_decision",
            {"execution_id": str(returned.pk), "decision": "approved"},
        )
        second = await gate_at(1, 1)
        assert second.caused_by == {}
        await temporal_env.client.get_workflow_handle(second.collection_workflow_id).signal(
            "human_gate_decision",
            {"execution_id": str(second.pk), "decision": "approved"},
        )
        result = await asyncio.wait_for(handle.result(), 30)
        history = await handle.fetch_history()
    assert result.ok and result.data["final_output"]["finished_count"] == 2
    visits = await rows(plan[2])
    assert [(row.collection_index, row.round_number) for row in visits[1:]] == [
        (0, 1),
        (0, 1),
        (0, 2),
        (0, 2),
        (1, 1),
        (1, 1),
    ]
    assert all(row.fanout_parent_execution_id is None for row in visits)
    await replay_family(temporal_env, history)


async def test_cancel_pending_serial_body_closes_parent_and_item_without_starting_later_item(temporal_env):
    from temporalio.client import WorkflowFailureError
    from temporalio.exceptions import CancelledError

    plan = await sync_to_async(create_plan)(kind="human_gate")
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=REGISTERED
    ):
        handle = await start(temporal_env, plan, {"items": [{"text": "pending"}, {"text": "never"}]})
        first = await wait_gate(plan[2], 0)
        for _ in range(200):
            child_history = await temporal_env.client.get_workflow_handle(
                first.collection_workflow_id
            ).fetch_history()
            if any(event.event_type == EventType.EVENT_TYPE_TIMER_STARTED for event in child_history.events):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("the item never durably waited before parent cancellation")
        await handle.cancel()
        with pytest.raises(WorkflowFailureError) as failure:
            await asyncio.wait_for(handle.result(), 30)
        assert isinstance(failure.value.cause, CancelledError)
        history = await handle.fetch_history()
    executions = await rows(plan[2])
    assert len(executions) == 2 and all(
        row.status == "cancelled" and row.ended_at is not None for row in executions
    )
    assert executions[1].collection_index == 0
    assert await sync_to_async(lambda: WorkflowRun.objects.get(pk=plan[2]).status)() == "cancelled"
    await replay_family(temporal_env, history)


async def test_abort_signal_settles_pending_collection_child_without_gate_timeout_or_next_item(
    temporal_env,
):
    plan = await sync_to_async(create_plan)(kind="human_gate")
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=REGISTERED
    ):
        handle = await start(temporal_env, plan, {"items": [{"text": "pending"}, {"text": "never"}]})
        first = await wait_gate(plan[2], 0)
        child = temporal_env.client.get_workflow_handle(first.collection_workflow_id)
        for _ in range(200):
            child_history = await child.fetch_history()
            if any(event.event_type == EventType.EVENT_TYPE_TIMER_STARTED for event in child_history.events):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("the real item did not enter its durable gate wait")
        await handle.signal("abort")
        with temporal_env.auto_time_skipping_disabled():
            result = await asyncio.wait_for(handle.result(), 3)
        history = await handle.fetch_history()
        child_history = await child.fetch_history()
    assert not result.ok and result.data["status"] == "incomplete"
    assert result.data["finished_count"] == 0
    executions = await rows(plan[2])
    assert len(executions) == 2
    assert executions[0].status == "failed" and executions[0].ended_at is not None
    assert executions[0].output["complete"] is False
    # Abort is a failed workflow, while the blocked SDK child is cancelled.
    assert executions[1].status == "failed" and executions[1].ended_at is not None
    assert executions[1].collection_index == 0
    assert await sync_to_async(lambda: WorkflowRun.objects.get(pk=plan[2]).status)() == "failed"
    assert child_history.events[-1].event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CANCELED
    assert result.message == "aborted by signal"
    await replay_family(temporal_env, history)
