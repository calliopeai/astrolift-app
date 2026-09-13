from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.core.cache import cache
from django.utils import timezone
from temporalio import workflow
from temporalio.client import WorkflowFailureError

from astrolift_identity.models import Organization
from astrolift_operations.models import WorkflowRun
from astrolift_workflows.activities import workflow_run_reconcile as reconcile
from astrolift_workflows.schedule_registry import ScheduleKind, get_schedule
from astrolift_workflows.workflows.workflow_run_reconcile_tick import WorkflowRunReconcileTickWorkflow
from core.testing.temporal import temporal_worker
from workflows.models import WorkflowDefinition, WorkflowInstance, WorkflowStage, WorkflowStageExecution


@workflow.defn(name="WorkflowDefinitionRunWorkflow", sandboxed=False)
class HoldingDefinition:
    def __init__(self):
        self.finished = False

    @workflow.run
    async def run(self):
        await workflow.wait_condition(lambda: self.finished)

    @workflow.signal
    def finish(self):
        self.finished = True


@pytest.fixture(autouse=True)
def reset_cursor():
    cache.delete(reconcile._CURSOR_KEY)
    yield
    cache.delete(reconcile._CURSOR_KEY)


def make_run(**overrides):
    key = uuid4().hex
    values = {
        "organization": Organization.objects.create(name="Reconcile", slug=f"reconcile-{key}"),
        "workflow_kind": "WorkflowDefinitionRunWorkflow",
        "workflow_id": f"workflow-{key}",
        "run_id": f"execution-{key}",
    }
    values.update(overrides)
    return WorkflowRun.objects.create(**values)


def identity(run):
    return reconcile.RunIdentity(run.pk, run.organization_id, run.workflow_id, run.run_id, run.version)


def instance(run, **overrides):
    values = {
        "organization": run.organization,
        "temporal_workflow_id": run.workflow_id,
        "temporal_run_id": run.run_id,
        "current_state": "running",
    }
    values.update(overrides)
    return WorkflowInstance.objects.create(**values)


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "terminated", "timed_out"])
def test_closed_observation_repairs_only_exact_instance(status):
    run = make_run(status="completed", ended_at=timezone.now())
    own = instance(run, current_state="completed", completed_at=run.ended_at)
    sibling = instance(run, temporal_run_id="another-incarnation")
    foreign = instance(run, organization=make_run().organization)
    closed_at = timezone.now() + timedelta(seconds=5)
    assert reconcile._apply_observation(identity(run), status, closed_at)
    run.refresh_from_db()
    own.refresh_from_db()
    assert (run.status, run.ended_at) == (status, closed_at)
    assert (own.current_state, own.completed_at) == (status, closed_at)
    for other in (sibling, foreign):
        other.refresh_from_db()
        assert (other.current_state, other.completed_at) == ("running", None)
    version = own.version
    assert not reconcile._apply_observation(identity(run), status, closed_at)
    own.refresh_from_db()
    assert own.version == version


@pytest.mark.django_db
def test_rechecks_identity_after_external_read():
    run = make_run()
    before = identity(run)
    run.run_id = "replacement-incarnation"
    run.save(update_fields=["run_id"])
    assert reconcile._apply_observation(before, "cancelled", timezone.now()) is None
    run.refresh_from_db()
    assert run.status == "running"
    assert run.ended_at is None


@pytest.mark.django_db
def test_closes_open_stages_and_preserves_completed_output():
    run = make_run()
    definition = WorkflowDefinition.objects.create(name="Reconcile", slug="reconcile-stages")
    stage = WorkflowStage.objects.create(
        definition=definition, slug="reconcile-gate", order=0, kind="human_gate"
    )
    opened = WorkflowStageExecution.objects.create(
        workflow_run=run, stage=stage, slug="reconcile-open", status="running"
    )
    completed = WorkflowStageExecution.objects.create(
        workflow_run=run, stage=stage, slug="reconcile-complete", status="completed", output={"keep": True}
    )
    closed_at = timezone.now()
    assert reconcile._apply_observation(identity(run), "terminated", closed_at)
    opened.refresh_from_db()
    completed.refresh_from_db()
    assert (opened.status, opened.ended_at) == ("cancelled", closed_at)
    assert (completed.status, completed.output) == ("completed", {"keep": True})


@pytest.mark.django_db
def test_bounded_cursor_rotates_past_perpetually_running_rows(monkeypatch):
    monkeypatch.setattr(reconcile, "_BATCH_SIZE", 2)
    rows = [make_run() for _ in range(5)]
    seen = []
    for _ in range(3):
        batch = reconcile._next_batch()
        assert len(batch) <= 2
        seen.extend(row.pk for row in batch)
    assert seen == [run.pk for run in rows]
    assert reconcile._next_batch()[0].pk == rows[0].pk


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("action", ["complete", "cancel", "terminate", "running"])
async def test_real_temporal_execution_repairs_mirror_and_configured_record(
    temporal_env, monkeypatch, action
):
    async def client():
        return temporal_env.client

    monkeypatch.setattr("astrolift_workflows.client._get_client_async", client)
    monkeypatch.setattr("astrolift_workflows.client._temporal_enabled", lambda: True)
    async with temporal_worker(temporal_env, workflows=[HoldingDefinition]):
        handle = await temporal_env.client.start_workflow(
            HoldingDefinition.run, id=f"reconcile-{uuid4()}", task_queue="astrolift-test"
        )
        run = await sync_to_async(make_run)(
            workflow_id=handle.id,
            run_id=handle.first_execution_run_id,
            status="completed",
            ended_at=timezone.now(),
        )
        own = await sync_to_async(instance)(run, current_state="completed", completed_at=run.ended_at)
        try:
            if action == "complete":
                await handle.signal(HoldingDefinition.finish)
                await asyncio.wait_for(handle.result(), 10)
            elif action in {"cancel", "terminate"}:
                await getattr(handle, action)()
                with pytest.raises(WorkflowFailureError):
                    await asyncio.wait_for(handle.result(), 10)
            desc = await handle.describe()
            result = await reconcile.reconcile_workflow_runs()
            await sync_to_async(run.refresh_from_db)()
            await sync_to_async(own.refresh_from_db)()
            expected = {
                "complete": "completed",
                "cancel": "cancelled",
                "terminate": "terminated",
                "running": "running",
            }[action]
            assert result.repaired == 1
            assert result.errors == 0
            assert (run.status, run.ended_at) == (expected, desc.close_time)
            assert (own.current_state, own.completed_at) == (expected, desc.close_time)
        finally:
            if action == "running":
                await handle.terminate()


@pytest.mark.django_db(transaction=True)
async def test_tick_uses_old_execution_when_workflow_id_is_reused(temporal_env, monkeypatch):
    async def client():
        return temporal_env.client

    monkeypatch.setattr("astrolift_workflows.client._get_client_async", client)
    monkeypatch.setattr("astrolift_workflows.client._temporal_enabled", lambda: True)
    async with temporal_worker(
        temporal_env,
        workflows=[HoldingDefinition, WorkflowRunReconcileTickWorkflow],
        activities=[reconcile.reconcile_workflow_runs_tick],
    ):
        workflow_id = f"reused-{uuid4()}"
        old = await temporal_env.client.start_workflow(
            HoldingDefinition.run, id=workflow_id, task_queue="astrolift-test"
        )
        await old.terminate()
        new = await temporal_env.client.start_workflow(
            HoldingDefinition.run, id=workflow_id, task_queue="astrolift-test"
        )
        run = await sync_to_async(make_run)(workflow_id=workflow_id, run_id=old.first_execution_run_id)
        try:
            result = await asyncio.wait_for(
                temporal_env.client.execute_workflow(
                    WorkflowRunReconcileTickWorkflow.run, id=f"tick-{uuid4()}", task_queue="astrolift-test"
                ),
                15,
            )
            await sync_to_async(run.refresh_from_db)()
            assert result.repaired == 1
            assert run.status == "terminated"
            assert (await new.describe()).status.name == "RUNNING"
        finally:
            await new.terminate()


@pytest.mark.django_db(transaction=True)
async def test_missing_execution_does_not_fabricate_completion(temporal_env, monkeypatch):
    async def client():
        return temporal_env.client

    monkeypatch.setattr("astrolift_workflows.client._get_client_async", client)
    monkeypatch.setattr("astrolift_workflows.client._temporal_enabled", lambda: True)
    run = await sync_to_async(make_run)()
    result = await reconcile.reconcile_workflow_runs()
    assert result.errors == 1
    await sync_to_async(run.refresh_from_db)()
    assert (run.status, run.ended_at) == ("running", None)


@pytest.mark.django_db(transaction=True)
async def test_blank_identity_is_skipped(temporal_env, monkeypatch):
    async def client():
        return temporal_env.client

    monkeypatch.setattr("astrolift_workflows.client._get_client_async", client)
    monkeypatch.setattr("astrolift_workflows.client._temporal_enabled", lambda: True)
    run = await sync_to_async(make_run)(run_id="")
    result = await reconcile.reconcile_workflow_runs()
    assert result.skipped == 1
    assert result.errors == 0
    await sync_to_async(run.refresh_from_db)()
    assert run.status == "running"


def test_reconciler_is_registered_for_worker_and_periodic_schedule():
    from astrolift_workflows.schedule_boot import resolve_active_kinds
    from astrolift_workflows.worker import ACTIVITIES, WORKFLOWS

    schedule = get_schedule(kind=ScheduleKind.WORKFLOW_RUN_RECONCILE)
    assert schedule.workflow_name == "WorkflowRunReconcileTickWorkflow"
    assert schedule.interval_seconds == 60
    assert ScheduleKind.WORKFLOW_RUN_RECONCILE in resolve_active_kinds(None)
    assert WorkflowRunReconcileTickWorkflow in WORKFLOWS
    assert reconcile.reconcile_workflow_runs_tick in ACTIVITIES


@pytest.mark.django_db
def test_running_observation_cannot_undo_concurrent_finalization():
    from astrolift_workflows.activities.workflow_stage_activities import _mark_workflow_run_sync

    run = make_run()
    before = identity(run)
    _mark_workflow_run_sync(str(run.pk), "cancelled", None, None)
    assert reconcile._apply_observation(before, "running", None) is None
    run.refresh_from_db()
    assert run.status == "cancelled"
    assert run.ended_at is not None


@pytest.mark.django_db
def test_new_inserts_and_terminal_history_do_not_starve_active_runs(monkeypatch):
    monkeypatch.setattr(reconcile, "_BATCH_SIZE", 2)
    monkeypatch.setattr(reconcile, "_AUDIT_BATCH_SIZE", 1)
    for _ in range(5):
        make_run(status="completed", ended_at=timezone.now())
    active = [make_run() for _ in range(3)]
    first = reconcile._next_batch()
    assert [row.pk for row in first[:2]] == [run.pk for run in active[:2]]
    for _ in range(5):
        make_run()
    assert reconcile._next_batch()[0].pk == active[2].pk
    assert reconcile._next_batch()[0].pk == active[0].pk


@pytest.mark.django_db(transaction=True)
async def test_disabled_runtime_leaves_database_untouched(monkeypatch):
    monkeypatch.setattr("astrolift_workflows.client._temporal_enabled", lambda: False)
    run = await sync_to_async(make_run)()
    assert (await reconcile.reconcile_workflow_runs()).evaluated == 0
    await sync_to_async(run.refresh_from_db)()
    assert (run.status, run.ended_at) == ("running", None)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("boundary", ["mismatched_identity", "missing_close_time", "rpc_failure"])
async def test_untrusted_or_unavailable_observation_preserves_record(monkeypatch, boundary):
    from types import SimpleNamespace

    run = await sync_to_async(make_run)()

    class BoundaryClient:
        def get_workflow_handle(self, workflow_id, *, run_id):
            assert (workflow_id, run_id) == (run.workflow_id, run.run_id)
            return self

        async def describe(self, *, rpc_timeout):
            assert rpc_timeout == timedelta(seconds=3)
            if boundary == "rpc_failure":
                raise TimeoutError("Temporal unavailable")
            return SimpleNamespace(
                id=run.workflow_id,
                run_id="wrong" if boundary == "mismatched_identity" else run.run_id,
                workflow_type="WorkflowDefinitionRunWorkflow",
                status=SimpleNamespace(name="CANCELED"),
                close_time=None,
            )

    async def client():
        return BoundaryClient()

    monkeypatch.setattr("astrolift_workflows.client._get_client_async", client)
    monkeypatch.setattr("astrolift_workflows.client._temporal_enabled", lambda: True)
    result = await reconcile.reconcile_workflow_runs()
    assert result.errors + result.skipped == 1
    assert result.repaired == 0
    await sync_to_async(run.refresh_from_db)()
    assert (run.status, run.ended_at) == ("running", None)
