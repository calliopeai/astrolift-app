"""Authenticated native/MCP gate decisions use the real worker and database."""

import asyncio
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone
from temporalio.api.enums.v1 import EventType

from astrolift_agents.tests.test_workflow_execution_mcp import (
    execution as execution_fixture,
)
from astrolift_agents.tests.test_workflow_execution_mcp import (
    mcp as mcp,
)
from astrolift_agents.tests.test_workflow_execution_mcp import (
    recorded_run,
    reviewed_args,
)
from astrolift_agents.tests.test_workflow_mcp import data, tool
from astrolift_workflows import client as engine
from astrolift_workflows.tests.test_human_gate_updates import gate_engine as gate_engine_fixture
from astrolift_workflows.tests.test_human_gate_updates import wait_gate
from astrolift_workflows.tests.test_serial_collections_temporal_2156 import REGISTERED
from astrolift_workflows.workflows.workflow_definition_run import WorkflowDefinitionRunWorkflow
from core.testing.temporal import temporal_worker
from workflows.models import WorkflowDefinition, WorkflowStage, WorkflowStageExecution
from workflows.stage_incarnations import StageIncarnation

pytestmark = pytest.mark.django_db(transaction=True)
execution = execution_fixture
gate_engine = gate_engine_fixture


@pytest.mark.parametrize("decision", ["approved", "rejected"])
@pytest.mark.parametrize("kind", ["root", "collection", "nested"])
async def test_mcp_explicit_decision_and_read_only_recovery(
    execution, gate_engine, monkeypatch, settings, decision, kind
):
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    settings.TEMPORAL_TASK_QUEUE = f"gate-mcp-{uuid4()}"

    async def real_client():
        return gate_engine.client

    monkeypatch.setattr(engine, "_get_client_async", real_client)

    def prepare():
        execution.stage.kind = "human_gate"
        execution.stage.approvers = [execution.user.email]
        execution.stage.timeout_seconds = 3600
        execution.stage.save()
        inputs = {"topic": "release notes"}
        if kind != "root":
            execution.definition.pattern_kind = "chained"
            execution.definition.input_schema["properties"]["items"] = {"type": "array"}
            execution.definition.save()
            inputs["items"] = [{"text": "one"}]
            execution.stage.kind = "collection"
            execution.stage.output_key = "each"
            execution.stage.iteration = {"max_items": 3, "items_path": "items", "body_end": "body"}
            execution.stage.save()
            body = WorkflowStage.objects.create(
                definition=execution.definition,
                order=1,
                kind="human_gate",
                output_key="body",
                timeout_seconds=3600,
                approvers=[execution.user.email],
            )
            if kind == "nested":
                child = WorkflowDefinition.objects.create(
                    organization=execution.world.org,
                    project=execution.world.medops_project,
                    name="Nested gate",
                    slug="nested-gate",
                    model_label="",
                )
                WorkflowStage.objects.create(
                    definition=child,
                    order=0,
                    kind="human_gate",
                    timeout_seconds=3600,
                    approvers=[execution.user.email],
                )
                body.kind = "workflow"
                body.workflow_ref = child.slug
                body.save()
        return reviewed_args(execution, inputs=inputs)

    args = await sync_to_async(prepare)()
    async with temporal_worker(
        gate_engine,
        task_queue=settings.TEMPORAL_TASK_QUEUE,
        workflows=[WorkflowDefinitionRunWorkflow],
        activities=REGISTERED,
    ):
        started = data(await sync_to_async(tool)(execution, "start_workflow_definition", **args))["data"]
        enclosing = gate_engine.client.get_workflow_handle(
            started["temporal_workflow_id"], run_id=started["temporal_run_id"]
        )
        row = await wait_gate(execution.world.org.pk)
        await sync_to_async(lambda: row.workflow_run)()
        root = gate_engine.client.get_workflow_handle(row.temporal_workflow_id, run_id=row.temporal_run_id)
        for _ in range(300):
            history = await root.fetch_history()
            if any(event.event_type == EventType.EVENT_TYPE_TIMER_STARTED for event in history.events):
                break
            await asyncio.sleep(0.02)
        ids = {"execution_id": str(row.workflow_run.guid), "stage_execution_id": str(row.guid)}
        pending = data(await sync_to_async(tool)(execution, "list_human_gates"))["gates"]
        assert pending[0]["execution_guid"] == str(row.guid) and "execution_id" not in pending[0]
        before = data(await sync_to_async(tool)(execution, "get_human_gate_decision", **ids))["gate"]
        assert before["request_state"] == "not_requested"
        submitted = data(
            await sync_to_async(tool)(
                execution,
                "decide_human_gate",
                **ids,
                temporal_run_id=row.temporal_run_id,
                decision=decision,
                note="User chose this",
                confirmed=True,
            )
        )
        assert submitted["ok"] and submitted["gate"]["request_state"] in {"requested", "recorded"}
        result = await asyncio.wait_for(root.result(), 20)
        assert result["ok"] is (decision == "approved")
        await asyncio.wait_for(enclosing.result(), 20)
        recovered = data(await sync_to_async(tool)(execution, "get_human_gate_decision", **ids))["gate"]
        assert recovered["request_state"] == "recorded"
        assert recovered["recorded_decision"] == recovered["requested_decision"] == decision
        assert recovered["decided_by_me"] is True
        assert "decided_by_user_id" not in recovered
        # Retrying a lost response acknowledges the recorded original decision.
        repeated = data(
            await sync_to_async(tool)(
                execution,
                "decide_human_gate",
                **ids,
                temporal_run_id=row.temporal_run_id,
                decision=decision,
                note="User chose this",
                confirmed=True,
            )
        )
        assert repeated["ok"] and repeated["gate"]["request_state"] == "recorded"
        conflict = await sync_to_async(tool)(
            execution,
            "decide_human_gate",
            **ids,
            temporal_run_id=row.temporal_run_id,
            decision="rejected" if decision == "approved" else "approved",
            note="User chose this",
            confirmed=True,
        )
        assert conflict["isError"]
        assert data(await sync_to_async(tool)(execution, "list_human_gates"))["gates"] == []
        await sync_to_async(row.refresh_from_db)()
        assert row.output["human_gate"]["decided_by_user_id"] == execution.user.pk


def local_gate(execution):
    execution.stage.kind = "human_gate"
    execution.stage.save()
    run = recorded_run(execution)
    row = WorkflowStageExecution.objects.create(
        workflow_run=run,
        stage=execution.stage,
        status="running",
        **StageIncarnation("default", run.workflow_id, run.run_id, "3").fields(),
    )
    return run, row, {"execution_id": str(run.guid), "stage_execution_id": str(row.guid)}


@pytest.mark.parametrize("change", ["wrong_approver", "revoke_role", "token_scope", "other_team"])
def test_gate_tools_recheck_native_authority(execution, change):
    run, row, ids = local_gate(execution)
    if change == "wrong_approver":
        execution.stage.approvers = ["someone-else@example.test"]
        execution.stage.save()
    elif change == "revoke_role":
        execution.binding.deleted_at = timezone.now()
        execution.binding.save()
    elif change == "token_scope":
        execution.token.scopes = ["mcp:read"]
        execution.token.save()
    else:
        execution.token.team = execution.world.platform
        execution.token.save()
    read = tool(execution, "get_human_gate_decision", **ids)
    assert read["isError"] or data(read)["gate"] is None
    write = tool(
        execution,
        "decide_human_gate",
        **ids,
        temporal_run_id=row.temporal_run_id,
        decision="approved",
        confirmed=True,
    )
    assert write["isError"]
    row.refresh_from_db()
    assert row.status == "running" and row.output is None


def test_closed_runs_are_not_actionable_pending_gates(execution):
    run, row, ids = local_gate(execution)
    assert data(tool(execution, "list_human_gates"))["gates"]
    run.status = "cancelled"
    run.save()
    assert data(tool(execution, "list_human_gates"))["gates"] == []
    assert data(tool(execution, "get_human_gate_decision", **ids))["gate"]["request_state"] == "closed"


@pytest.mark.parametrize("change", ["unconfirmed", "impersonation", "wrong_run", "invalid_decision"])
def test_gate_request_validation_never_admits_invalid_input(execution, change):
    run, row, ids = local_gate(execution)
    args = {**ids, "temporal_run_id": row.temporal_run_id, "decision": "approved", "confirmed": True}
    if change == "unconfirmed":
        args["confirmed"] = False
    elif change == "impersonation":
        args["decided_by_user_id"] = 1
    elif change == "wrong_run":
        args["temporal_run_id"] = str(uuid4())
    else:
        args["decision"] = "maybe"
    assert tool(execution, "decide_human_gate", **args)["isError"]
    row.refresh_from_db()
    assert row.status == "running" and row.output is None


def test_pending_pages_continue_past_other_named_approvers(execution):
    from workflows.models import WorkflowStage

    run, wanted, _ = local_gate(execution)
    other = WorkflowStage.objects.create(
        definition=execution.definition, order=1, kind="human_gate", approvers=["other@example.test"]
    )
    WorkflowStageExecution.objects.create(workflow_run=run, stage=other, status="running", slug="other-gate")
    page = data(tool(execution, "list_human_gates", limit=1))
    assert page["gates"] == [] and page["next_cursor"]
    next_page = data(tool(execution, "list_human_gates", limit=1, cursor=page["next_cursor"]))
    assert [row["execution_guid"] for row in next_page["gates"]] == [str(wanted.guid)]
    assert next_page["next_cursor"] is None


async def test_real_update_timeout_preserves_exact_recovery_target(
    execution, gate_engine, monkeypatch, settings
):
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    settings.TEMPORAL_TASK_QUEUE = f"gate-timeout-{uuid4()}"

    async def real_client():
        return gate_engine.client

    monkeypatch.setattr(engine, "_get_client_async", real_client)

    def prepare():
        execution.stage.kind = "human_gate"
        execution.stage.timeout_seconds = 3600
        execution.stage.save()
        return reviewed_args(execution)

    args = await sync_to_async(prepare)()
    worker_args = {
        "task_queue": settings.TEMPORAL_TASK_QUEUE,
        "workflows": [WorkflowDefinitionRunWorkflow],
        "activities": REGISTERED,
    }
    async with temporal_worker(gate_engine, **worker_args):
        data(await sync_to_async(tool)(execution, "start_workflow_definition", **args))
        row = await wait_gate(execution.world.org.pk)
        run = await sync_to_async(lambda: row.workflow_run)()
        root = gate_engine.client.get_workflow_handle(row.temporal_workflow_id, run_id=row.temporal_run_id)
        for _ in range(300):
            if any(
                event.event_type == EventType.EVENT_TYPE_TIMER_STARTED
                for event in (await root.fetch_history()).events
            ):
                break
            await asyncio.sleep(0.02)
    ids = {"execution_id": str(run.guid), "stage_execution_id": str(row.guid)}
    call = {**ids, "temporal_run_id": row.temporal_run_id, "decision": "approved", "confirmed": True}
    # The engine is real and reachable, but there is no worker to admit the update.
    uncertain = await sync_to_async(tool)(execution, "decide_human_gate", **call)
    assert uncertain["isError"]
    state = uncertain["structuredContent"]["gate"]
    assert state["request_state"] == "unknown"
    assert state["stage_execution_guid"] == str(row.guid)
    async with temporal_worker(gate_engine, **worker_args):
        recovered = data(await sync_to_async(tool)(execution, "get_human_gate_decision", **ids))["gate"]
        if recovered["request_state"] == "not_requested":
            data(await sync_to_async(tool)(execution, "decide_human_gate", **call))
        else:
            assert recovered["request_state"] in {"requested", "recorded"}
        assert (await asyncio.wait_for(root.result(), 20))["ok"]
        final = data(await sync_to_async(tool)(execution, "get_human_gate_decision", **ids))["gate"]
    assert final["request_state"] == "recorded" and final["recorded_decision"] == "approved"
