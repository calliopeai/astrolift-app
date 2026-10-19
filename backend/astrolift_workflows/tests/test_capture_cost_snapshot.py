"""Retired cost schedule histories complete without financial calls or new rows."""

from uuid import uuid4

import pytest
from temporalio.worker import Replayer

from astrolift_workflows.activities.scheduled import capture_platform_cost_snapshot
from astrolift_workflows.schedule_boot import _allowlisted_catalog, resolve_active_kinds
from astrolift_workflows.schedule_registry import DEFAULT_SCHEDULES, ScheduleKind
from astrolift_workflows.worker import ACTIVITIES, WORKFLOWS
from astrolift_workflows.workflows.scheduled import CapturePlatformCostSnapshotWorkflow
from core.testing.temporal import temporal_worker


def test_retired_cost_schedule_cannot_be_recreated_even_by_old_operator_allowlist():
    assert all(row.kind != ScheduleKind.CAPTURE_PLATFORM_COST_SNAPSHOT for row in DEFAULT_SCHEDULES)
    selected = resolve_active_kinds("capture_platform_cost_snapshot,capture_quota_usage_snapshot")
    catalog = _allowlisted_catalog(selected)
    assert [row.kind for row in catalog] == [ScheduleKind.CAPTURE_QUOTA_USAGE_SNAPSHOT]
    assert CapturePlatformCostSnapshotWorkflow in WORKFLOWS
    assert capture_platform_cost_snapshot in ACTIVITIES


@pytest.mark.asyncio
async def test_existing_cost_workflow_and_activity_names_are_replay_compatible_noops(
    temporal_env, monkeypatch
):
    from aws.cost import AWSBillingActuals
    from azure.cost import AzureBillingActuals
    from gcp.cost import GCPBillingActuals

    def forbidden(*args, **kwargs):
        pytest.fail("retired workflow invoked provider financial API")

    for adapter in (AWSBillingActuals, GCPBillingActuals, AzureBillingActuals):
        monkeypatch.setattr(adapter, "query_actuals_by_service", forbidden)
    queue = f"retired-cost-{uuid4()}"
    async with temporal_worker(
        temporal_env,
        task_queue=queue,
        workflows=[CapturePlatformCostSnapshotWorkflow],
        activities=[capture_platform_cost_snapshot],
    ):
        handle = await temporal_env.client.start_workflow(
            CapturePlatformCostSnapshotWorkflow.run, id=f"retired-cost-{uuid4()}", task_queue=queue
        )
        result = await handle.result()
        assert result["ok"] if isinstance(result, dict) else result.ok
        history = await handle.fetch_history()
    await Replayer(workflows=[CapturePlatformCostSnapshotWorkflow]).replay_workflow(history)
    scheduled = [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]
    assert scheduled == ["astrolift.scheduled.capture_platform_cost_snapshot"]
