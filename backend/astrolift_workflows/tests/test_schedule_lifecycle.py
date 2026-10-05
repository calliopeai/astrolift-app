"""Engine observations and recovery remain distinct from saved configuration."""

import dataclasses

import pytest
from asgiref.sync import async_to_sync
from temporalio.client import ScheduleSpec, ScheduleUpdate
from temporalio.service import RPCError, RPCStatusCode

from astrolift_workflows.tests.schedule_server import schedule_server as schedule_server_fixture
from astrolift_workflows.tests.test_configured_workflow_schedule_2053 import org as org_fixture
from astrolift_workflows.tests.test_configured_workflow_schedule_2053 import scheduled as scheduled_fixture
from workflows.schedule_sync import (
    desired_revision,
    observe_workflow_schedule,
    reconcile_workflow_schedule,
    schedule_id_for,
    sync_workflow_schedule,
)

schedule_server = schedule_server_fixture
org = org_fixture
scheduled = scheduled_fixture

pytestmark = pytest.mark.django_db(transaction=True)


def test_create_change_and_disable_preserve_engine_identity(scheduled, schedule_server):
    schedule_server.track(scheduled)
    missing = observe_workflow_schedule(scheduled)
    assert missing.observed_state == "missing" and not missing.confirmed
    created = sync_workflow_schedule(scheduled)
    assert created.confirmed and created.observed_state == "active", created
    scheduled.refresh_from_db()
    assert scheduled.schedule_managed
    assert scheduled.schedule_revision == desired_revision(scheduled)
    assert observe_workflow_schedule(scheduled).confirmed
    action = schedule_server.action_input(scheduled)
    assert action["schedule_revision"] == desired_revision(scheduled)
    scheduled.schedule_cron = "0 12 1 1 *"
    scheduled.save()
    assert observe_workflow_schedule(scheduled).observed_state == "drifted"
    changed = sync_workflow_schedule(scheduled)
    assert changed.confirmed, changed
    assert changed.engine_created_at == created.engine_created_at
    scheduled.refresh_from_db()
    assert observe_workflow_schedule(scheduled).confirmed
    scheduled.is_enabled = False
    scheduled.save()
    disabled = sync_workflow_schedule(scheduled)
    assert disabled.confirmed and disabled.observed_state == "missing"
    assert schedule_server.describe(scheduled) is None
    scheduled.refresh_from_db()
    assert not scheduled.schedule_managed and not scheduled.schedule_revision


def test_engine_spec_drift_requires_reconciliation(scheduled, schedule_server):
    schedule_server.track(scheduled)
    assert sync_workflow_schedule(scheduled).confirmed
    scheduled.refresh_from_db()

    @async_to_sync
    async def drift():
        client = await schedule_server.connect()
        await client.get_schedule_handle(schedule_id_for(scheduled)).update(
            lambda input: ScheduleUpdate(
                dataclasses.replace(
                    input.description.schedule,
                    spec=ScheduleSpec(cron_expressions=["0 1 1 1 *"]),
                )
            )
        )

    drift()
    observed = observe_workflow_schedule(scheduled)
    assert not observed.confirmed and observed.observed_state == "drifted"
    assert sync_workflow_schedule(scheduled).confirmed


def test_stale_version_or_activation_intent_never_applies(scheduled, schedule_server):
    schedule_server.track(scheduled)
    version = scheduled.version
    scheduled.schedule_cron = "0 12 1 1 *"
    scheduled.save()
    for expected_version, expected_active in [(version, True), (scheduled.version, False)]:
        result = reconcile_workflow_schedule(
            scheduled, expected_version=expected_version, expected_active=expected_active
        )
        assert not result.confirmed and result.error_code == "stale_configuration"
    assert schedule_server.describe(scheduled) is None


def test_invalid_cron_is_saved_but_never_confirmed(scheduled, schedule_server):
    schedule_server.track(scheduled)
    scheduled.schedule_cron = "not a cron expression"
    scheduled.save()
    result = sync_workflow_schedule(scheduled)
    assert result.error_code == "invalid_schedule" and not result.confirmed
    assert schedule_server.describe(scheduled) is None
    scheduled.refresh_from_db()
    assert scheduled.schedule_managed


@pytest.mark.parametrize("operation", ["create_schedule", "update", "delete"])
def test_lost_response_after_real_acceptance_is_recoverable(
    scheduled, schedule_server, monkeypatch, operation
):
    from temporalio.client import Client, ScheduleHandle

    schedule_server.track(scheduled)
    target = Client if operation == "create_schedule" else ScheduleHandle
    original = getattr(target, operation)
    if operation != "create_schedule":
        assert sync_workflow_schedule(scheduled).confirmed
        scheduled.refresh_from_db()
        if operation == "delete":
            scheduled.is_enabled = False
        else:
            scheduled.schedule_cron = "0 12 1 1 *"
        scheduled.save()

    async def accepted_then_timeout(self, *args, **kwargs):
        await original(self, *args, **kwargs)
        raise TimeoutError("private upstream credential detail")

    with monkeypatch.context() as patch:
        patch.setattr(target, operation, accepted_then_timeout)
        result = sync_workflow_schedule(scheduled)
    assert not result.confirmed and result.error_code == "engine_timeout"
    assert "private" not in repr(result)
    scheduled.refresh_from_db()
    assert scheduled.schedule_managed
    engine = schedule_server.describe(scheduled)
    assert (engine is not None) == (operation != "delete")
    recovered = sync_workflow_schedule(scheduled)
    assert recovered.confirmed
    if engine:
        assert recovered.engine_created_at == engine.info.created_at


def test_engine_outage_never_looks_like_absence(scheduled, schedule_server, monkeypatch):
    from temporalio.client import ScheduleHandle

    schedule_server.track(scheduled)
    assert sync_workflow_schedule(scheduled).confirmed
    scheduled.refresh_from_db()
    scheduled.is_enabled = False
    scheduled.save()

    async def unavailable(*args, **kwargs):
        raise RPCError("private engine details", RPCStatusCode.UNAVAILABLE, b"")

    with monkeypatch.context() as patch:
        patch.setattr(ScheduleHandle, "describe", unavailable)
        result = sync_workflow_schedule(scheduled)
        assert not result.confirmed and result.observed_state == "unknown"
        assert result.error_code == "engine_unavailable"
    scheduled.refresh_from_db()
    assert scheduled.schedule_managed and schedule_server.describe(scheduled)
    assert sync_workflow_schedule(scheduled).confirmed


def test_a_replaced_action_revision_cannot_fire(scheduled, schedule_server):
    from astrolift_workflows.activities.configured_workflow_schedule_fire import _create_sync

    schedule_server.track(scheduled)
    assert sync_workflow_schedule(scheduled).confirmed
    old = schedule_server.action_input(scheduled)
    scheduled.schedule_cron = "0 12 1 1 *"
    scheduled.save()
    fire = _create_sync(old)
    assert fire.run_input is None and fire.skipped == "schedule configuration changed"
    assert not scheduled.runs.exists()


@pytest.mark.asyncio
async def test_actual_schedule_fires_create_distinct_completed_runs(scheduled, schedule_server):
    import asyncio

    from asgiref.sync import sync_to_async
    from temporalio.client import WorkflowExecutionStatus
    from temporalio.worker import Worker

    from astrolift_operations.models import WorkflowRun
    from astrolift_workflows.activities.configured_workflow_schedule_fire import (
        create_scheduled_workflow_run,
        record_scheduled_workflow_start,
    )
    from astrolift_workflows.activities.workflow_stage_activities import (
        get_workflow_stages,
        mark_workflow_run,
        snapshot_checkpoint,
    )
    from astrolift_workflows.workflows import (
        ConfiguredWorkflowScheduleWorkflow,
        WorkflowDefinitionRunWorkflow,
    )

    scheduled.schedule_cron = "0 0 1 1 *"
    await sync_to_async(scheduled.save)()
    schedule_server.track(scheduled)
    assert (await sync_to_async(sync_workflow_schedule)(scheduled)).confirmed
    client = await schedule_server.connect()
    handle = client.get_schedule_handle(schedule_id_for(scheduled))
    async with Worker(
        client,
        task_queue=schedule_server.task_queue,
        workflows=[ConfiguredWorkflowScheduleWorkflow, WorkflowDefinitionRunWorkflow],
        activities=[
            create_scheduled_workflow_run,
            record_scheduled_workflow_start,
            get_workflow_stages,
            snapshot_checkpoint,
            mark_workflow_run,
        ],
    ):
        for count in (1, 2):
            await handle.trigger()
            async with asyncio.timeout(60):
                while True:
                    description = await handle.describe()
                    actions = description.info.recent_actions
                    if len(actions) == count and not description.info.running_actions:
                        statuses = [
                            (
                                await client.get_workflow_handle(
                                    item.action.workflow_id,
                                    run_id=item.action.first_execution_run_id,
                                ).describe()
                            ).status
                            for item in actions
                        ]
                        if all(status == WorkflowExecutionStatus.COMPLETED for status in statuses):
                            break
                    await asyncio.sleep(0.1)
        runs = await sync_to_async(list)(
            WorkflowRun.objects.filter(workflow_definition=scheduled.definition).values(
                "pk", "status", "run_id"
            )
        )
        assert len(runs) == 2 and len({run["run_id"] for run in runs}) == 2
        assert all(run["status"] == "completed" for run in runs)
        assert (await sync_to_async(observe_workflow_schedule)(scheduled)).action_count == 2


def test_configuration_writes_wait_for_reconciliation(scheduled, schedule_server, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from django.db import close_old_connections, connection
    from temporalio.client import Client

    from workflows.models import Workflow

    schedule_server.track(scheduled)
    entered = Event()
    release = Event()
    writer_connected = Event()
    writer_pid = []
    original = Client.create_schedule

    async def held_create(self, *args, **kwargs):
        import asyncio

        entered.set()
        assert await asyncio.to_thread(release.wait, 10)
        return await original(self, *args, **kwargs)

    def reconcile():
        close_old_connections()
        try:
            return sync_workflow_schedule(scheduled)
        finally:
            connection.close()

    def change():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                writer_pid.append(cursor.fetchone()[0])
            writer_connected.set()
            Workflow.objects.filter(pk=scheduled.pk).update(is_enabled=False, version=scheduled.version + 1)
        finally:
            connection.close()

    monkeypatch.setattr(Client, "create_schedule", held_create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reconciliation = pool.submit(reconcile)
        assert entered.wait(5)
        writer = pool.submit(change)
        try:
            assert writer_connected.wait(5)
            import time

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [writer_pid[0]]
                    )
                    if cursor.fetchone()[0] == "Lock":
                        break
                time.sleep(0.01)
            else:
                pytest.fail("configuration writer did not wait for the schedule row lock")
            assert not writer.done()
        finally:
            release.set()
        first = reconciliation.result(timeout=10)
        writer.result(timeout=10)
    assert first.confirmed and first.configuration_version == scheduled.version
    scheduled.refresh_from_db()
    assert not scheduled.is_enabled and not observe_workflow_schedule(scheduled).confirmed
    assert sync_workflow_schedule(scheduled).confirmed


def test_operator_cleanup_failure_is_reported_and_exact_id_retry_works(
    scheduled, schedule_server, monkeypatch
):
    from io import StringIO

    from django.core.management import CommandError, call_command
    from temporalio.client import ScheduleHandle

    schedule_server.track(scheduled)
    assert sync_workflow_schedule(scheduled).confirmed
    scheduled.refresh_from_db()
    scheduled.is_enabled = False
    scheduled.save()

    async def unavailable(*args, **kwargs):
        raise RPCError("private engine details", RPCStatusCode.UNAVAILABLE, b"")

    out = StringIO()
    with monkeypatch.context() as patch:
        patch.setattr(ScheduleHandle, "delete", unavailable)
        with pytest.raises(CommandError, match="1 schedule"):
            call_command(
                "resync_workflow_schedules", "--apply", "--workflow-id", str(scheduled.guid), stdout=out
            )
    assert "1 failed; absence confirmed for 0" in out.getvalue()
    assert "private" not in out.getvalue()
    assert schedule_server.describe(scheduled) is not None
    out = StringIO()
    call_command("resync_workflow_schedules", "--apply", "--workflow-id", str(scheduled.guid), stdout=out)
    assert "0 failed; absence confirmed for 1" in out.getvalue()
    assert schedule_server.describe(scheduled) is None
