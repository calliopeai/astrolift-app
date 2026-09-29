"""InstallClusterPrereqsWorkflow passes the requesting org to the run record (#1955).

A shared cluster has no org of its own, so the bootstrap-run row can only
be attributed to the org that asked for the install if the workflow hands
that org to the record activity. Runs the real workflow on a Temporal test
server with the two activities faked.
"""

from __future__ import annotations

from temporalio import activity

from astrolift_workflows.inputs import Actor, InstallClusterPrereqsInput
from astrolift_workflows.workflows.install_cluster_prereqs import InstallClusterPrereqsWorkflow
from core.testing.temporal import temporal_worker


def _input() -> InstallClusterPrereqsInput:
    return InstallClusterPrereqsInput(
        cluster_id=7,
        actor=Actor(kind="user", user_id=3),
        selected_components=("cert-manager",),
        option_overrides={},
        organization_id=42,
    )


def _recorder(recorded: list):
    @activity.defn(name="astrolift.cluster.record_bootstrap_run")
    async def record(
        cluster_id: int,
        actor_user_id: int | None,
        status: str,
        applied: list[dict[str, str]],
        error_message: str,
        started_at_iso: str,
        ended_at_iso: str,
        organization_id: int | None = None,
    ) -> None:
        recorded.append((cluster_id, actor_user_id, status, organization_id))

    return record


async def test_a_successful_install_records_the_requesting_org(temporal_env):
    recorded: list = []

    @activity.defn(name="astrolift.cluster.install_prereqs")
    async def install(
        cluster_id: int, selected_keys: list[str], option_overrides: dict, additive: bool = False
    ) -> dict:
        return {"applied": [{"name": "cert-manager", "version": "1.15.0"}], "skipped": []}

    async with temporal_worker(
        temporal_env, workflows=[InstallClusterPrereqsWorkflow], activities=[install, _recorder(recorded)]
    ):
        result = await temporal_env.client.execute_workflow(
            InstallClusterPrereqsWorkflow.run,
            _input(),
            id="install-prereqs-org-ok",
            task_queue="astrolift-test",
        )

    assert result.ok is True
    assert recorded == [(7, 3, "succeeded", 42)]


async def test_a_failed_install_records_the_requesting_org(temporal_env):
    recorded: list = []

    @activity.defn(name="astrolift.cluster.install_prereqs")
    async def install(
        cluster_id: int, selected_keys: list[str], option_overrides: dict, additive: bool = False
    ) -> dict:
        raise RuntimeError("apply refused")

    async with temporal_worker(
        temporal_env, workflows=[InstallClusterPrereqsWorkflow], activities=[install, _recorder(recorded)]
    ):
        result = await temporal_env.client.execute_workflow(
            InstallClusterPrereqsWorkflow.run,
            _input(),
            id="install-prereqs-org-fails",
            task_queue="astrolift-test",
        )

    assert result.ok is False
    assert recorded == [(7, 3, "failed", 42)]
