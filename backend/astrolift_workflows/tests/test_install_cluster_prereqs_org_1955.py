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


async def test_crd_readiness_survives_more_than_three_attempts(temporal_env):
    from temporalio.exceptions import ApplicationError

    recorded: list = []
    attempts = 0

    @activity.defn(name="astrolift.cluster.install_prereqs")
    async def install(
        cluster_id: int, selected_keys: list[str], option_overrides: dict, additive: bool = False
    ) -> dict:
        nonlocal attempts
        attempts += 1
        if attempts <= 4:
            raise ApplicationError(
                "operator CRDs not registered", type="BootstrapReadinessPending", non_retryable=True
            )
        return {"applied": [{"name": "cert-manager", "version": "1.15.0"}], "skipped": []}

    async with temporal_worker(
        temporal_env, workflows=[InstallClusterPrereqsWorkflow], activities=[install, _recorder(recorded)]
    ):
        result = await temporal_env.client.execute_workflow(
            InstallClusterPrereqsWorkflow.run,
            _input(),
            id="bootstrap-delayed-crds",
            task_queue="astrolift-test",
        )
    assert result.ok is True
    assert attempts == 5
    assert recorded == [(7, 3, "succeeded", 42)]


async def test_readiness_wait_is_bounded_and_keeps_the_underlying_diagnostic(temporal_env):
    from temporalio.exceptions import ApplicationError

    recorded: list = []
    attempts = 0

    @activity.defn(name="astrolift.cluster.install_prereqs")
    async def install(
        cluster_id: int, selected_keys: list[str], option_overrides: dict, additive: bool = False
    ) -> dict:
        nonlocal attempts
        attempts += 1
        raise ApplicationError(
            "KnativeServing CRD is absent", type="BootstrapReadinessPending", non_retryable=True
        )

    async with temporal_worker(
        temporal_env, workflows=[InstallClusterPrereqsWorkflow], activities=[install, _recorder(recorded)]
    ):
        result = await temporal_env.client.execute_workflow(
            InstallClusterPrereqsWorkflow.run,
            _input(),
            id="bootstrap-readiness-deadline",
            task_queue="astrolift-test",
        )
    assert result.ok is False
    assert 3 < attempts <= 40
    assert "readiness deadline exceeded" in result.message
    assert "KnativeServing CRD is absent" in result.message
    assert recorded == [(7, 3, "failed", 42)]


async def test_permanent_failure_is_not_retried_as_readiness(temporal_env):
    from temporalio.exceptions import ApplicationError

    recorded: list = []
    attempts = 0

    @activity.defn(name="astrolift.cluster.install_prereqs")
    async def install(
        cluster_id: int, selected_keys: list[str], option_overrides: dict, additive: bool = False
    ) -> dict:
        nonlocal attempts
        attempts += 1
        raise ApplicationError(
            "iam:CreateRole denied for the controller", type="AccessDenied", non_retryable=True
        )

    async with temporal_worker(
        temporal_env, workflows=[InstallClusterPrereqsWorkflow], activities=[install, _recorder(recorded)]
    ):
        result = await temporal_env.client.execute_workflow(
            InstallClusterPrereqsWorkflow.run,
            _input(),
            id="bootstrap-permanent-diagnostic",
            task_queue="astrolift-test",
        )
    assert result.ok is False
    assert attempts == 1
    assert "iam:CreateRole denied for the controller" in result.message
    assert "Activity task failed" not in result.message
    assert recorded == [(7, 3, "failed", 42)]


async def test_existing_bootstrap_history_replays_without_the_new_wait_path(temporal_env, monkeypatch):
    from temporalio import workflow
    from temporalio.worker import Replayer

    @activity.defn(name="astrolift.cluster.install_prereqs")
    async def install(
        cluster_id: int, selected_keys: list[str], option_overrides: dict, additive: bool = False
    ) -> dict:
        return {"applied": [], "skipped": []}

    with monkeypatch.context() as previous_worker:
        previous_worker.setattr(workflow, "patched", lambda _patch_id: False)
        async with temporal_worker(
            temporal_env, workflows=[InstallClusterPrereqsWorkflow], activities=[install, _recorder([])]
        ):
            handle = await temporal_env.client.start_workflow(
                InstallClusterPrereqsWorkflow.run,
                _input(),
                id="bootstrap-before-readiness-patch",
                task_queue="astrolift-test",
            )
            assert (await handle.result()).ok is True
            history = await handle.fetch_history()

    await Replayer(workflows=[InstallClusterPrereqsWorkflow]).replay_workflow(history)
