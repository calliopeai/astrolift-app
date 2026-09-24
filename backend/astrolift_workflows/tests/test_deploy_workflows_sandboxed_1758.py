"""Deploy, promote and rollback on the workflow runner production uses (#1758).

The worker runs every workflow in Temporal's default sandbox, which refuses
things a direct ``run()`` call with a stubbed ``execute_activity`` lets
through. These tests run the real workflow classes in that sandbox, on the
time-skipping test server, against fake activities that record the order the
workflow scheduled them in.
"""

from __future__ import annotations

import asyncio

from temporalio import activity
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
from temporalio.workflow import NondeterminismError

from astrolift_workflows.inputs import Actor, PromoteInput
from astrolift_workflows.workflows.promote_deployment import PromoteDeploymentWorkflow

TASK_QUEUE = "astrolift-test"

_ACTOR = Actor(kind="system")


def fake_activities(calls: list[str]) -> list:
    """Stand-ins for every activity deploy, promote and rollback schedule.
    Each records its name, so a test can read the order the workflow chose."""

    @activity.defn(name="astrolift.app.resync_manifest_for_deploy")
    async def resync_manifest_for_deploy(deployment_id: int) -> dict:
        calls.append("resync_manifest_for_deploy")
        return {"status": "in_sync", "workload_count": 1}

    @activity.defn(name="astrolift.deploy.pre_flight")
    async def pre_flight(deployment_id: int) -> None:
        calls.append("pre_flight")

    @activity.defn(name="astrolift.deploy.mark_deploying")
    async def mark_deploying(deployment_id: int) -> None:
        calls.append("mark_deploying")

    @activity.defn(name="astrolift.build.fetch_app_build_strategy")
    async def fetch_app_build_strategy(registered_app_id: int) -> str:
        calls.append("fetch_app_build_strategy")
        return "off"

    @activity.defn(name="astrolift.app.provision_namespace")
    async def provision_namespace(registered_app_id: int, app_environment_id: int | None = None) -> None:
        calls.append("provision_namespace")

    @activity.defn(name="astrolift.deploy.ensure_cloudfront_cert")
    async def ensure_cloudfront_cert(deployment_id: int) -> dict:
        calls.append("ensure_cloudfront_cert")
        return {}

    @activity.defn(name="astrolift.deploy.ensure_static_site_services")
    async def ensure_static_site_services(deployment_id: int) -> dict:
        calls.append("ensure_static_site_services")
        return {}

    @activity.defn(name="astrolift.deploy.ensure_faas_services")
    async def ensure_faas_services(deployment_id: int) -> dict:
        calls.append("ensure_faas_services")
        return {}

    @activity.defn(name="astrolift.workload_identity.ensure")
    async def ensure_workload_identity(registered_app_id: int, app_environment_id: int) -> dict:
        calls.append("ensure_workload_identity")
        return {}

    @activity.defn(name="astrolift.deploy.render_manifests")
    async def render_manifests(deployment_id: int) -> dict:
        calls.append("render_manifests")
        return {}

    @activity.defn(name="astrolift.deploy.apply_manifests")
    async def apply_manifests(deployment_id: int) -> dict:
        calls.append("apply_manifests")
        return {"created": [], "updated": [], "unchanged": []}

    @activity.defn(name="astrolift.deploy.update_secrets")
    async def update_secrets(deployment_id: int) -> int:
        calls.append("update_secrets")
        return 1

    @activity.defn(name="astrolift.deploy.sync_static_assets")
    async def sync_static_assets(inp: dict) -> dict:
        calls.append("sync_static_assets")
        return {}

    @activity.defn(name="astrolift.deploy.ensure_static_dns")
    async def ensure_static_dns(deployment_id: int) -> dict:
        calls.append("ensure_static_dns")
        return {}

    @activity.defn(name="astrolift.deploy.wait_dns")
    async def wait_dns(deployment_id: int, timeout_seconds: int = 300) -> int:
        calls.append("wait_dns")
        return 0

    @activity.defn(name="astrolift.deploy.poll_rollout")
    async def poll_rollout(deployment_id: int, timeout_seconds: int = 600) -> bool:
        calls.append("poll_rollout")
        return True

    @activity.defn(name="astrolift.deploy.health_check")
    async def health_check(deployment_id: int) -> bool:
        calls.append("health_check")
        return True

    @activity.defn(name="astrolift.deploy.mark_running")
    async def mark_running(deployment_id: int) -> None:
        calls.append("mark_running")

    @activity.defn(name="astrolift.deploy.mark_failed")
    async def mark_failed(deployment_id: int, reason: str = "") -> None:
        calls.append("mark_failed")

    @activity.defn(name="astrolift.deploy.create_promotion_deployment")
    async def create_promotion_deployment(source_deployment_id: int, target_app_environment_id: int) -> int:
        calls.append("create_promotion_deployment")
        return 30

    @activity.defn(name="astrolift.deploy.evaluate_supply_chain_gate")
    async def evaluate_supply_chain_gate(deployment_id: int) -> dict:
        calls.append("evaluate_supply_chain_gate")
        return {"decision": "pass"}

    @activity.defn(name="astrolift.deploy.create_rollback_deployment")
    async def create_rollback_deployment(deployment_id: int) -> int:
        calls.append("create_rollback_deployment")
        return 31

    return [
        resync_manifest_for_deploy,
        pre_flight,
        mark_deploying,
        fetch_app_build_strategy,
        provision_namespace,
        ensure_cloudfront_cert,
        ensure_static_site_services,
        ensure_faas_services,
        ensure_workload_identity,
        render_manifests,
        apply_manifests,
        update_secrets,
        sync_static_assets,
        ensure_static_dns,
        wait_dns,
        poll_rollout,
        health_check,
        mark_running,
        mark_failed,
        create_promotion_deployment,
        evaluate_supply_chain_gate,
        create_rollback_deployment,
    ]


async def run_sandboxed(temporal_env, workflow_cls, workflow_input, workflow_id: str):
    """Run one workflow to completion in the sandbox.

    Returns (result, recorded activity names, workflow handle)."""
    calls: list[str] = []
    worker = Worker(
        temporal_env.client,
        task_queue=TASK_QUEUE,
        workflows=[workflow_cls],
        activities=fake_activities(calls),
        # Worker's default, spelled out: the runner production uses.
        workflow_runner=SandboxedWorkflowRunner(),
        # A sandbox or determinism violation fails the run at once instead
        # of retrying the workflow task until the test is killed.
        workflow_failure_exception_types=[NondeterminismError],
    )
    async with worker:
        handle = await temporal_env.client.start_workflow(
            workflow_cls.run,
            workflow_input,
            id=workflow_id,
            task_queue=TASK_QUEUE,
        )
        result = await asyncio.wait_for(handle.result(), timeout=180)
    return result, calls, handle


async def test_a_promotion_runs_to_completion_on_the_sandboxed_runner(temporal_env):
    """run() imported asgiref after the supply-chain gate. The sandbox
    refuses that import, because asgiref subclasses the restricted
    concurrent.futures.Executor, so a promotion failed its workflow task
    there and retried it forever. The direct run() call in
    test_supply_chain_gate never enters the sandbox, so it passed."""
    result, calls, _handle = await run_sandboxed(
        temporal_env,
        PromoteDeploymentWorkflow,
        PromoteInput(source_deployment_id=3, target_app_environment_id=4, actor=_ACTOR),
        "promote-on-the-sandboxed-runner",
    )

    assert result.ok is True, result.message
    assert calls[-1] == "mark_running"
