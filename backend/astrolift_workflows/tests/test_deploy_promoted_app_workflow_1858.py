"""DeployPromotedAppWorkflow under a real Temporal test server (#1858).

The builder workflows' catch-alls record a failure on the dev env. Under
Temporal's default unlimited retries a failing activity never raised into
them; the bounded policy is what makes the failure land, with the
activity's own message rather than "Activity task failed".
"""

from __future__ import annotations

from temporalio import activity

from astrolift_workflows.inputs import DeployPromotedAppInput
from astrolift_workflows.workflows.dev_environment import DeployPromotedAppWorkflow
from core.testing.temporal import temporal_worker


async def test_deploy_promoted_app_passes_the_storage_decision_through(temporal_env):
    calls = []

    @activity.defn(name="astrolift.builder.deploy_promoted_app")
    async def deploy(dev_environment_id: int, storage_class: str) -> dict:
        calls.append((dev_environment_id, storage_class))
        return {"namespace": "acme-shop", "app_url": "https://acme-shop.example.test"}

    @activity.defn(name="astrolift.builder.mark_dev_environment_failed")
    async def mark_failed(dev_environment_id: int, error: str) -> None:
        raise AssertionError("a successful deploy must not mark the dev env failed")

    async with temporal_worker(
        temporal_env, workflows=[DeployPromotedAppWorkflow], activities=[deploy, mark_failed]
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
