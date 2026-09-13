"""Opt-in verification against a real Docker daemon and a disposable sleep image."""

from __future__ import annotations

import os
import subprocess

import pytest

from astrolift_agents.models import AgentEnvironmentSpec, AgentTask, DispatcherInstance
from astrolift_agents.services.task_target import spawner_for_task
from astrolift_agents.services.workflow_task_cleanup import cleanup_workflow_tasks
from astrolift_dispatch.tests.docker_proxy import DockerProxy
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


@pytest.mark.parametrize("connection_mode", ["host", "context"])
def test_stop_retries_original_connection_and_preserves_neighbor(
    run,  # noqa: F811
    definition,  # noqa: F811
    monkeypatch,
    tmp_path,
    connection_mode,
):
    with DockerProxy() as proxy:
        _check_cleanup(run, definition, monkeypatch, tmp_path, connection_mode, proxy)


def _check_cleanup(run, definition, monkeypatch, tmp_path, connection_mode, proxy):  # noqa: F811
    for key in ("DOCKER_CONTEXT", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path))
    if connection_mode == "context":
        monkeypatch.delenv("DOCKER_HOST", raising=False)
        subprocess.run(
            ["docker", "context", "create", "original", "--docker", f"host={proxy.endpoint}"],
            check=True,
            capture_output=True,
        )
        monkeypatch.setenv("DOCKER_CONTEXT", "original")
    else:
        monkeypatch.setenv("DOCKER_HOST", proxy.endpoint)
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
        assert own.dispatch_target["docker_connection"]["mode"] == connection_mode
        assert own.dispatch_target["docker_connection"]["endpoint"] == proxy.endpoint
        assert own_spawner.status(own.external_id).running
        assert other_spawner.status(other.external_id).running
        monkeypatch.setenv("DOCKER_HOST", "unix:///missing/default.sock")
        monkeypatch.setenv("DOCKER_CONTEXT", "missing-default-context")
        monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path / "changed-default"))
        monkeypatch.setenv("DOCKER_TLS_VERIFY", "1")
        monkeypatch.setenv("DOCKER_CERT_PATH", "/missing/default/certs")
        proxy.block_deletes = True
        _mark_workflow_run_sync(str(run.pk), "cancelled", None, None)
        run.refresh_from_db()
        assert run.status == "cancelled"
        assert run.failure["task_cleanup"]["status"] == "failed"
        assert run.failure["task_cleanup"]["remaining"] == 1
        own.refresh_from_db()
        assert own.status == "running"
        assert own_spawner.status(own.external_id).running
        assert other_spawner.status(other.external_id).running
        cleanup_workflow_tasks(run.pk)
        run.refresh_from_db()
        assert run.failure["task_cleanup"]["status"] == "failed"
        assert not own_spawner.confirm_stopped(own.external_id)
        proxy.block_deletes = False
        cleanup_workflow_tasks(run.pk)
        own.refresh_from_db()
        other.refresh_from_db()
        run.refresh_from_db()
        assert own.status == "cancelled"
        assert own_spawner.confirm_stopped(own.external_id)
        assert other.status == "running"
        assert other_spawner.status(other.external_id).running
        assert run.failure["task_cleanup"]["status"] == "completed"
        assert run.failure["task_cleanup"]["remaining"] == 0
        deletions = [
            (path.split("/")[-1].split("?")[0], blocked)
            for method, path, blocked in proxy.requests
            if method == "DELETE"
        ]
        assert len(deletions) == 3 and [blocked for _, blocked in deletions] == [True, True, False]
        assert len({container for container, _ in deletions}) == 1
        assert len(deletions[0][0]) == 64
        _mark_workflow_run_sync(str(neighbor.pk), "cancelled", None, None)
        other.refresh_from_db()
        assert other.status == "cancelled"
        assert other_spawner.confirm_stopped(other.external_id)
    finally:
        proxy.block_deletes = False
        # Confine cleanup to this test's durable task IDs, even after an assertion fails.
        for task in AgentTask.objects.filter(agent_run__stage_executions__workflow_run__in=[run, neighbor]):
            if task.dispatch_target:
                spawner_for_task(task).stop(
                    task.external_id or task.dispatch_target["planned_external_id"],
                    expected_task_guid=str(task.guid),
                )
