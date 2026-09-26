"""DeployPromotedAppWorkflow under a real Temporal test server (#1858, #1875).

The builder workflows' catch-alls record a failure on the dev env. Under
Temporal's default unlimited retries a failing activity never raised into
them; the bounded policy is what makes the failure land, with the
activity's own message rather than "Activity task failed".

#1875 adds a second activity, ``record_promoted_app_deployment``, gated
behind ``workflow.patched`` since it is a new step in the workflow's own
activity sequence. See ``test_deploy_promoted_app_replay_1875.py`` for the
determinism/replay coverage of that gate; these tests cover the live
scheduling + failure-handling behavior only.
"""

from __future__ import annotations

from temporalio import activity

from astrolift_workflows.inputs import DeployPromotedAppInput
from astrolift_workflows.workflows.dev_environment import DeployPromotedAppWorkflow
from core.testing.temporal import temporal_worker


async def test_deploy_promoted_app_passes_the_storage_decision_through(temporal_env):
    calls = []
    record_calls = []

    @activity.defn(name="astrolift.builder.deploy_promoted_app")
    async def deploy(dev_environment_id: int, storage_class: str) -> dict:
        calls.append((dev_environment_id, storage_class))
        return {"namespace": "acme-shop", "app_url": "https://acme-shop.example.test"}

    @activity.defn(name="astrolift.builder.record_promoted_app_deployment")
    async def record(dev_environment_id: int, storage_class: str) -> dict:
        record_calls.append((dev_environment_id, storage_class))
        return {"workload_id": 1, "deployment_id": 1}

    @activity.defn(name="astrolift.builder.mark_dev_environment_failed")
    async def mark_failed(dev_environment_id: int, error: str) -> None:
        raise AssertionError("a successful deploy must not mark the dev env failed")

    async with temporal_worker(
        temporal_env, workflows=[DeployPromotedAppWorkflow], activities=[deploy, record, mark_failed]
    ):
        result = await temporal_env.client.execute_workflow(
            DeployPromotedAppWorkflow.run,
            DeployPromotedAppInput(dev_environment_id=7, storage_class="gp3"),
            id="deploy-promoted-app-ok",
            task_queue="astrolift-test",
        )

    assert result.ok is True
    assert result.data == {"namespace": "acme-shop", "app_url": "https://acme-shop.example.test"}
    assert calls == [(7, "gp3")]
    # #1875: the recording step runs after a successful deploy, with the
    # same args -- it re-derives the app from the dev env row itself.
    assert record_calls == [(7, "gp3")]


async def test_a_failure_recording_the_workload_still_marks_the_dev_environment_failed(temporal_env):
    """The apply can succeed while the new #1875 bookkeeping step fails (a
    DB hiccup, say); the workflow's one catch-all still covers it, same as
    any other activity in this workflow."""
    record_calls = []
    failed = []

    @activity.defn(name="astrolift.builder.deploy_promoted_app")
    async def deploy(dev_environment_id: int, storage_class: str) -> dict:
        return {"namespace": "acme-shop", "app_url": "https://acme-shop.example.test"}

    @activity.defn(name="astrolift.builder.record_promoted_app_deployment")
    async def record(dev_environment_id: int, storage_class: str) -> dict:
        record_calls.append(dev_environment_id)
        raise RuntimeError("could not resolve an app_environment to record against")

    @activity.defn(name="astrolift.builder.mark_dev_environment_failed")
    async def mark_failed(dev_environment_id: int, error: str) -> None:
        failed.append((dev_environment_id, error))

    async with temporal_worker(
        temporal_env, workflows=[DeployPromotedAppWorkflow], activities=[deploy, record, mark_failed]
    ):
        result = await temporal_env.client.execute_workflow(
            DeployPromotedAppWorkflow.run,
            DeployPromotedAppInput(dev_environment_id=9, storage_class=""),
            id="deploy-promoted-app-record-fails",
            task_queue="astrolift-test",
        )

    assert result.ok is False
    assert len(record_calls) == 5  # bounded retry policy, same as deploy_promoted_app
    reason = "RuntimeError: could not resolve an app_environment to record against"
    assert failed == [(9, reason)]
    assert result.message == reason


async def test_deploy_promoted_app_records_the_failure_after_bounded_retries(temporal_env):
    attempts = []
    failed = []

    @activity.defn(name="astrolift.builder.deploy_promoted_app")
    async def deploy(dev_environment_id: int, storage_class: str) -> dict:
        attempts.append(dev_environment_id)
        raise RuntimeError("apply refused: PersistentVolumeClaim/builder-app-data")

    @activity.defn(name="astrolift.builder.mark_dev_environment_failed")
    async def mark_failed(dev_environment_id: int, error: str) -> None:
        failed.append((dev_environment_id, error))

    async with temporal_worker(
        temporal_env, workflows=[DeployPromotedAppWorkflow], activities=[deploy, mark_failed]
    ):
        result = await temporal_env.client.execute_workflow(
            DeployPromotedAppWorkflow.run,
            DeployPromotedAppInput(dev_environment_id=7, storage_class=""),
            id="deploy-promoted-app-fails",
            task_queue="astrolift-test",
        )

    assert result.ok is False
    assert len(attempts) == 5
    reason = "RuntimeError: apply refused: PersistentVolumeClaim/builder-app-data"
    assert failed == [(7, reason)]
    assert result.message == reason
