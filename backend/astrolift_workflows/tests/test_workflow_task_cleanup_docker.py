"""Opt-in verification against a real Docker daemon and a disposable sleep image."""

from __future__ import annotations

import os

import pytest

from astrolift_agents.models import AgentEnvironmentSpec, AgentTask, DispatcherInstance
from astrolift_agents.services.task_target import spawner_for_task
from astrolift_operations.models import WorkflowRun
from astrolift_workflows.activities.workflow_stage_activities import _mark_workflow_run_sync
from astrolift_workflows.tests.test_workflow_stage_activities import (  # noqa: F401
    _dispatch_agent_stage,
    agent_workload,
    definition,
    org,
    run,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        not os.getenv("ASTROLIFT_TEST_AGENT_IMAGE"), reason="Requires a disposable Docker image"
    ),
]


def test_stop_removes_owned_container_and_preserves_neighbor(run, definition):  # noqa: F811
    environment = AgentEnvironmentSpec.objects.create(
        organization=run.organization,
        name="Cleanup smoke",
        slug="cleanup-smoke",
        image_tag=os.environ["ASTROLIFT_TEST_AGENT_IMAGE"],
    )
    stage = definition.stages.get(order=0)
    stage.environment_spec_slug = environment.slug
    stage.save(update_fields=["environment_spec_slug", "updated_at", "version"])
    DispatcherInstance.objects.create(
        organization=run.organization,
        name="Cleanup Docker",
        slug="cleanup-docker",
        endpoint="http://localhost:9000",
        cloud=DispatcherInstance.Cloud.LOCAL,
        backend=DispatcherInstance.Backend.LOCAL_DOCKER,
        status=DispatcherInstance.Status.ACTIVE,
    )
    neighbor = WorkflowRun.objects.create(
        organization=run.organization,
        workflow_kind=run.workflow_kind,
        workflow_id="neighbor-docker-workflow",
        run_id="neighbor-execution",
    )
    try:
        _, own = _dispatch_agent_stage(run, definition, 0)
        _, other = _dispatch_agent_stage(neighbor, definition, 0)
        own_spawner, other_spawner = spawner_for_task(own), spawner_for_task(other)
        assert own_spawner.status(own.external_id).running
        assert other_spawner.status(other.external_id).running
        _mark_workflow_run_sync(str(run.pk), "cancelled", None, None)
        own.refresh_from_db()
        other.refresh_from_db()
        run.refresh_from_db()
        assert own.status == "cancelled"
        assert own_spawner.confirm_stopped(own.external_id)
        assert other.status == "running"
        assert other_spawner.status(other.external_id).running
        assert run.failure["task_cleanup"]["status"] == "completed"
        _mark_workflow_run_sync(str(neighbor.pk), "cancelled", None, None)
        other.refresh_from_db()
        assert other.status == "cancelled"
        assert other_spawner.confirm_stopped(other.external_id)
    finally:
        # Confine cleanup to this test's durable task IDs, even after an assertion fails.
        for task in AgentTask.objects.filter(agent_run__stage_executions__workflow_run__in=[run, neighbor]):
            if task.dispatch_target:
                spawner_for_task(task).stop(
                    task.external_id or task.dispatch_target["planned_external_id"],
                    expected_task_guid=str(task.guid),
                )
