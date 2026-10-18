"""AppExpiryWorkflow (#2433) on a time-skipping Temporal server: the timer
fires and tears down through DeregisterAppWorkflow with the data kept, a
reschedule moves it, a worker restart mid-timer keeps it, and the kept
managed services are purged after the grace period.

The activities are fakes registered under the real names, driven by a
script of ``next_step`` answers; the decisions themselves are tested
against the database in ``astrolift_registry/tests/test_temporary_apps_2433.py``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from astrolift_workflows.inputs import AppExpiryInput, DeregisterAppInput, WorkflowResult
from astrolift_workflows.workflows.app_expiry import AppExpiryWorkflow

DAY = 86400


class Script:
    """``next_step`` answers in order, and what each activity saw."""

    def __init__(self, steps: list[dict[str, Any]]):
        self.steps = list(steps)
        self.step_times: list[Any] = []
        self.calls: list[tuple[str, Any]] = []
        self.namespace_failures = 0

    def activities(self):
        script = self

        @activity.defn(name="astrolift.app.expiry_next_step")
        async def next_step(app_id: int) -> dict[str, Any]:
            script.step_times.append(activity.info().current_attempt_scheduled_time)
            return script.steps.pop(0) if script.steps else {"action": "done"}

        @activity.defn(name="astrolift.app.expiry_fire")
        async def fire(app_id: int) -> bool:
            script.calls.append(("fire", app_id))
            return True

        @activity.defn(name="astrolift.app.expiry_record_teardown")
        async def record_teardown(app_id: int, teardown: dict[str, Any] | None) -> None:
            script.calls.append(("record_teardown", teardown))

        @activity.defn(name="astrolift.app.expiry_record_purge")
        async def record_purge(app_id: int, results: list[dict[str, Any]]) -> None:
            script.calls.append(("record_purge", results))

        @activity.defn(name="astrolift.managed_service.deprovision")
        async def deprovision(ms_id: int, delete_data: bool, force_destroy: bool) -> dict[str, Any]:
            script.calls.append(("deprovision", (ms_id, delete_data, force_destroy)))
            return {"ok": True, "message": "deleted"}

        @activity.defn(name="astrolift.managed_service.finalize_deletion")
        async def finalize(ms_id: int) -> None:
            script.calls.append(("finalize", ms_id))

        @activity.defn(name="astrolift.app.expiry_purge_namespaces")
        async def purge_namespaces(app_id: int) -> None:
            script.calls.append(("purge_namespaces", app_id))
            if script.namespace_failures:
                script.namespace_failures -= 1
                raise RuntimeError("native namespace deletion unconfirmed")

        return [next_step, fire, record_teardown, record_purge, deprovision, finalize, purge_namespaces]


@workflow.defn(name="DeregisterAppWorkflow")
class FakeDeregister:
    @workflow.run
    async def run(self, input: DeregisterAppInput) -> WorkflowResult:
        return WorkflowResult(
            ok=True,
            data={
                "teardown": {"deployments_cancelled": {"ok": True}},
                "still_live_resources": [],
                "input": {
                    "keep_managed_services": input.keep_managed_services,
                    "skip_grace": input.skip_grace,
                    "delete_data": input.delete_data,
                },
            },
        )


def _worker(env, queue, script):
    return Worker(
        env.client,
        task_queue=queue,
        workflows=[AppExpiryWorkflow, FakeDeregister],
        activities=script.activities(),
        workflow_runner=UnsandboxedWorkflowRunner(),
        # No sticky cache: a restarted worker replays the history from the
        # server, as a real one does once the dead worker's sticky queue lapses.
        max_cached_workflows=0,
    )


TEARDOWN = {"action": "teardown", "guid": "g-1", "delete_data": False}


async def test_timer_fires_tears_down_keeping_data_then_purges(temporal_env):
    script = Script(
        [
            {"action": "sleep", "seconds": 2 * DAY},
            {"action": "fire"},
            TEARDOWN,
            {"action": "sleep", "seconds": 7 * DAY},
            {"action": "purge", "managed_service_ids": [41]},
            {"action": "done"},
        ]
    )
    queue = f"expiry-{uuid.uuid4()}"
    async with _worker(temporal_env, queue, script):
        result = await asyncio.wait_for(
            temporal_env.client.execute_workflow(
                AppExpiryWorkflow.run,
                AppExpiryInput(registered_app_id=7),
                id=f"AppExpiryWorkflow-{queue}",
                task_queue=queue,
            ),
            timeout=60,
        )
    assert result.ok
    names = [name for name, _ in script.calls]
    assert names == ["fire", "record_teardown", "deprovision", "finalize", "purge_namespaces", "record_purge"]
    teardown = dict(script.calls)["record_teardown"]
    assert teardown["input"] == {"keep_managed_services": True, "skip_grace": True, "delete_data": False}
    assert dict(script.calls)["deprovision"] == (41, True, False)
    # The 2-day timer and the 7-day grace both ran on the server's clock.
    assert script.step_times[1] - script.step_times[0] >= timedelta(days=2)
    assert script.step_times[4] - script.step_times[3] >= timedelta(days=7)


async def test_delete_data_on_expiry_deletes_with_the_app(temporal_env):
    script = Script([{"action": "fire"}, {**TEARDOWN, "delete_data": True}, {"action": "done"}])
    queue = f"expiry-{uuid.uuid4()}"
    async with _worker(temporal_env, queue, script):
        await asyncio.wait_for(
            temporal_env.client.execute_workflow(
                AppExpiryWorkflow.run,
                AppExpiryInput(registered_app_id=7),
                id=f"AppExpiryWorkflow-{queue}",
                task_queue=queue,
            ),
            timeout=60,
        )
    teardown = dict(script.calls)["record_teardown"]
    assert teardown["input"] == {"keep_managed_services": False, "skip_grace": True, "delete_data": True}


async def test_namespace_purge_failure_keeps_retention_and_retries_after_an_hour(temporal_env):
    script = Script(
        [
            {"action": "purge", "managed_service_ids": [41]},
            {"action": "purge", "managed_service_ids": []},
            {"action": "done"},
        ]
    )
    script.namespace_failures = 3
    queue = f"expiry-{uuid.uuid4()}"
    async with _worker(temporal_env, queue, script):
        result = await asyncio.wait_for(
            temporal_env.client.execute_workflow(
                AppExpiryWorkflow.run,
                AppExpiryInput(registered_app_id=7),
                id=f"AppExpiryWorkflow-{queue}",
                task_queue=queue,
            ),
            timeout=60,
        )
    assert result.ok
    assert [name for name, _ in script.calls].count("deprovision") == 1
    purges = [data for name, data in script.calls if name == "record_purge"]
    assert purges[0][-1]["namespace_cleanup"] and not purges[0][-1]["ok"]
    assert purges[1] == []
    assert script.step_times[1] - script.step_times[0] >= timedelta(hours=1)


async def test_reschedule_wakes_the_timer(temporal_env):
    script = Script([{"action": "sleep", "seconds": 30 * DAY}, {"action": "done"}])
    queue = f"expiry-{uuid.uuid4()}"
    async with _worker(temporal_env, queue, script):
        handle = await temporal_env.client.start_workflow(
            AppExpiryWorkflow.run,
            AppExpiryInput(registered_app_id=7),
            id=f"AppExpiryWorkflow-{queue}",
            task_queue=queue,
        )
        for _ in range(100):
            if script.step_times:
                break
            await asyncio.sleep(0.05)
        await handle.signal(AppExpiryWorkflow.reschedule)
        await asyncio.wait_for(handle.result(), timeout=60)
    assert len(script.step_times) == 2
    assert script.step_times[1] - script.step_times[0] < timedelta(days=1)


async def test_reschedule_during_row_read_invalidates_the_old_sleep(temporal_env):
    script = Script([])
    reading, release = asyncio.Event(), asyncio.Event()

    @activity.defn(name="astrolift.app.expiry_next_step")
    async def held_read(app_id: int) -> dict[str, Any]:
        script.step_times.append(activity.info().current_attempt_scheduled_time)
        if len(script.step_times) == 1:
            reading.set()
            await release.wait()
            return {"action": "sleep", "seconds": 30 * DAY}
        return {"action": "done"}

    activities = [held_read, *script.activities()[1:]]
    queue = f"expiry-{uuid.uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[AppExpiryWorkflow],
        activities=activities,
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await temporal_env.client.start_workflow(
            AppExpiryWorkflow.run,
            AppExpiryInput(registered_app_id=7),
            id=f"AppExpiryWorkflow-{queue}",
            task_queue=queue,
        )
        await asyncio.wait_for(reading.wait(), timeout=10)
        await handle.signal(AppExpiryWorkflow.reschedule)
        release.set()
        assert (await asyncio.wait_for(handle.result(), timeout=60)).ok
    assert len(script.step_times) == 2
    assert script.step_times[1] - script.step_times[0] < timedelta(days=1)


async def test_atomic_arm_preserves_running_timer_and_restarts_completed(temporal_env, monkeypatch):
    from asgiref.sync import sync_to_async

    from astrolift_registry.expiry import arm
    from astrolift_workflows import client

    async def current_client():
        return temporal_env.client

    monkeypatch.setattr(client, "_get_client_async", current_client)
    monkeypatch.setattr(client, "_temporal_enabled", lambda: True)
    queue = f"expiry-{uuid.uuid4()}"
    monkeypatch.setattr(client, "_task_queue", lambda: queue)
    script = Script([{"action": "sleep", "seconds": 30 * DAY}, {"action": "done"}])
    async with _worker(temporal_env, queue, script):
        # Reserve the first run before any worker can finish it.
        handle = await temporal_env.client.start_workflow(
            AppExpiryWorkflow.run,
            AppExpiryInput(registered_app_id=7),
            id=f"AppExpiryWorkflow-{queue}",
            task_queue=queue,
        )
        for _ in range(100):
            if script.step_times:
                break
            await asyncio.sleep(0.05)
        original = await handle.describe()
        await sync_to_async(arm)(7, queue)
        assert (await handle.describe()).run_id == original.run_id
        assert (await asyncio.wait_for(handle.result(), timeout=60)).ok
        # A permanent app's completed timer can later become temporary again.
        await sync_to_async(arm)(7, queue)
        replacement = temporal_env.client.get_workflow_handle(handle.id, result_type=WorkflowResult)
        assert (await replacement.describe()).run_id != original.run_id
        assert (await asyncio.wait_for(replacement.result(), timeout=60)).ok


async def test_a_worker_restart_mid_timer_keeps_it(temporal_env):
    script = Script(
        [{"action": "sleep", "seconds": 3 * DAY}, {"action": "fire"}, TEARDOWN, {"action": "done"}]
    )
    queue = f"expiry-{uuid.uuid4()}"
    async with _worker(temporal_env, queue, script):
        handle = await temporal_env.client.start_workflow(
            AppExpiryWorkflow.run,
            AppExpiryInput(registered_app_id=7),
            id=f"AppExpiryWorkflow-{queue}",
            task_queue=queue,
        )
        for _ in range(100):
            if script.step_times:
                break
            await asyncio.sleep(0.05)
    # The first worker is gone with the timer pending; a new one picks it up.
    async with _worker(temporal_env, queue, script):
        result = await asyncio.wait_for(handle.result(), timeout=60)
    assert result.ok
    assert [name for name, _ in script.calls] == ["fire", "record_teardown"]
    assert script.step_times[1] - script.step_times[0] >= timedelta(days=3)
