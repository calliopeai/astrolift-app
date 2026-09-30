"""Purged executions finish bounded owned-task cleanup without further RPCs."""

import pytest
from asgiref.sync import sync_to_async

from astrolift_workflows.activities import workflow_run_reconcile as reconcile
from astrolift_workflows.tests.test_workflow_stage_activities import (  # noqa: F401
    _dispatch_agent_stage,
    _fake_spawner,
    agent_workload,
    definition,
    org,
    run,
)


@pytest.mark.django_db(transaction=True)
async def test_expired_history_cleanup_continues_without_temporal(temporal_env, monkeypatch, run, definition):  # noqa: F811
    async def client():
        return temporal_env.client

    def dispatch():
        run.run_id = "missing-execution"
        run.save(update_fields=["run_id", "updated_at", "version"])
        return [_dispatch_agent_stage(run, definition, 0)[1] for _ in range(3)]

    tasks = await sync_to_async(dispatch)()
    monkeypatch.setattr("astrolift_workflows.client._get_client_async", client)
    monkeypatch.setattr("astrolift_workflows.client._temporal_enabled", lambda: True)
    first = await reconcile.reconcile_workflow_runs()
    await sync_to_async(run.refresh_from_db)()
    assert first.repaired == 1
    assert first.errors == 0
    assert run.status == "expired"
    assert run.failure["task_cleanup"]["remaining"] == 2

    async def unavailable():
        pytest.fail("expired history cleanup must not connect to Temporal")

    monkeypatch.setattr("astrolift_workflows.client._get_client_async", unavailable)
    for remaining in (1, 0):
        tick = await reconcile.reconcile_workflow_runs()
        assert tick.repaired == 1
        assert tick.errors == 0
        await sync_to_async(run.refresh_from_db)()
        assert run.failure["task_cleanup"]["remaining"] == remaining
    assert run.failure["task_cleanup"]["status"] == "completed"
    assert (await reconcile.reconcile_workflow_runs()).evaluated == 0
    for task in tasks:
        await sync_to_async(task.refresh_from_db)()
        assert task.status == "cancelled"
