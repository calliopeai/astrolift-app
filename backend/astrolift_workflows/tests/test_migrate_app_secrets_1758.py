"""MigrateAppWorkflow under a real Temporal test server (#1758 review, M1).

apply_to_target_cluster applies the workloads, whose envFrom names the
literal, bundle and binding Secrets, but nothing created those Secrets on
the target. The workflow now runs materialize_secrets_on_target first, and
a failure there stops the migration before the target gets any workloads.
"""

from __future__ import annotations

from temporalio import activity

from astrolift_workflows.inputs import Actor, MigrateAppInput
from astrolift_workflows.workflows.migrate_app import MigrateAppWorkflow
from core.testing.temporal import temporal_worker

_INPUT = MigrateAppInput(
    registered_app_id=1,
    app_environment_id=2,
    deployment_id=3,
    target_cluster_id=4,
    drain_source=False,
    actor=Actor(kind="system"),
)


def _activities(calls: list[tuple], *, secrets_error: Exception | None = None) -> list:
    @activity.defn(name="astrolift.migration.validate_target")
    async def validate(app_environment_id: int, target_cluster_id: int) -> None:
        calls.append(("validate", app_environment_id, target_cluster_id))

    @activity.defn(name="astrolift.migration.materialize_secrets_on_target")
    async def materialize(deployment_id: int, target_cluster_id: int) -> int:
        calls.append(("materialize", deployment_id, target_cluster_id))
        if secrets_error is not None:
            raise secrets_error
        return 1

    @activity.defn(name="astrolift.migration.apply_to_target")
    async def apply(deployment_id: int, target_cluster_id: int) -> dict:
        calls.append(("apply", deployment_id, target_cluster_id))
        return {"created": [], "updated": [], "unchanged": []}

    @activity.defn(name="astrolift.migration.poll_rollout_on_target")
    async def poll(deployment_id: int, target_cluster_id: int) -> bool:
        calls.append(("poll", deployment_id, target_cluster_id))
        return True

    @activity.defn(name="astrolift.migration.switch_binding")
    async def switch(app_environment_id: int, target_cluster_id: int) -> int:
        calls.append(("switch", app_environment_id, target_cluster_id))
        return 9

    return [validate, materialize, apply, poll, switch]


async def test_secrets_reach_the_target_before_its_workloads(temporal_env):
    calls: list[tuple] = []
    async with temporal_worker(temporal_env, workflows=[MigrateAppWorkflow], activities=_activities(calls)):
        result = await temporal_env.client.execute_workflow(
            MigrateAppWorkflow.run,
            _INPUT,
            id="migrate-app-secrets-before-workloads",
            task_queue="astrolift-test",
        )

    assert result.ok is True, result.message
    assert [call[0] for call in calls] == ["validate", "materialize", "apply", "poll", "switch"]
    assert ("materialize", 3, 4) in calls


async def test_a_secrets_failure_stops_the_migration_before_the_target_workloads(temporal_env):
    calls: list[tuple] = []
    activities = _activities(calls, secrets_error=RuntimeError("secrets backend unreachable"))
    async with temporal_worker(temporal_env, workflows=[MigrateAppWorkflow], activities=activities):
        result = await temporal_env.client.execute_workflow(
            MigrateAppWorkflow.run,
            _INPUT,
            id="migrate-app-secrets-fail",
            task_queue="astrolift-test",
        )

    assert result.ok is False
    assert "source still serving traffic" in result.message
    assert "apply" not in [call[0] for call in calls]
