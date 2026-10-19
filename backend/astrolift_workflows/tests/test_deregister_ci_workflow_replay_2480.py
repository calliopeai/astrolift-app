"""DeregisterAppWorkflow removes the managed CI workflow last (#2480).

Runs the real workflow class on the runner production uses (Temporal's
sandbox) on the time-skipping test server, against fake activities, then
replays the fresh history and one captured before the step existed.

The legacy fixture was captured by running the workflow exactly as it stood at
09e3f2c28 (this branch's base): a clean first teardown, with the grace timer,
one managed service and one materialized secret, against the real Temporal
test server, dumping ``handle.fetch_history()``. Worker identities were then
normalized to ``1@astrolift-worker``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer, Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
from temporalio.workflow import NondeterminismError

from astrolift_workflows.inputs import Actor, DeregisterAppInput
from astrolift_workflows.workflows.deregister_app import DeregisterAppWorkflow

TASK_QUEUE = "astrolift-test"
FIXTURES = Path(__file__).parent / "fixtures"

_INPUT = DeregisterAppInput(registered_app_id=7, actor=Actor(kind="user", display="operator"))

# What a clean first teardown scheduled before #2480, in order.
_BEFORE_2480 = [
    "astrolift.app.mark_tearing_down",
    "astrolift.app.abort_in_flight_deploys",
    "astrolift.app.delete_namespaces",
    "astrolift.app.list_managed_service_ids",
    "astrolift.managed_service.deprovision",
    "astrolift.managed_service.finalize_deletion",
    "astrolift.deploy.delete_static_dns_records",
    "astrolift.app.delete_namespaces",
    "astrolift.capability_deprovision.registry_repo",
    "astrolift.capability_deprovision.identity_role",
    "astrolift.app.list_secret_targets",
    "astrolift.secret_rotation.delete_from_cluster",
    "astrolift.app.revoke_secret_bundle_refs",
    "astrolift.app.delete_source_webhook",
    "astrolift.app.revoke_deploy_tokens",
    "astrolift.app.soft_delete_records",
    "astrolift.app.mark_deregistered",
]


def _scheduled(history: WorkflowHistory) -> list[str]:
    """Activity names, in the order the workflow scheduled them."""
    return [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    ]


async def _replay(history: WorkflowHistory) -> None:
    await Replayer(
        workflows=[DeregisterAppWorkflow], workflow_runner=SandboxedWorkflowRunner()
    ).replay_workflow(history)


def _fake_activities(remove_ci_workflow: Callable[[int], dict]) -> list:
    @activity.defn(name="astrolift.app.mark_tearing_down")
    async def mark_tearing_down(registered_app_id: int) -> bool:
        return False

    @activity.defn(name="astrolift.app.abort_in_flight_deploys")
    async def abort_in_flight_deploys(registered_app_id: int) -> list[str]:
        return []

    @activity.defn(name="astrolift.app.delete_namespaces")
    async def delete_namespaces(registered_app_id: int) -> list[str]:
        return ["dev-cluster/acme-hello"]

    @activity.defn(name="astrolift.app.list_managed_service_ids")
    async def list_managed_service_ids(registered_app_id: int) -> list[int]:
        return [11]

    @activity.defn(name="astrolift.managed_service.deprovision")
    async def deprovision(managed_service_id: int, delete_data: bool, force_destroy: bool) -> dict:
        return {"ok": True, "message": "snapshot retained"}

    @activity.defn(name="astrolift.managed_service.finalize_deletion")
    async def finalize(managed_service_id: int) -> None:
        return None

    @activity.defn(name="astrolift.deploy.delete_static_dns_records")
    async def delete_static_dns(registered_app_id: int) -> dict:
        return {"deleted": []}

    @activity.defn(name="astrolift.capability_deprovision.registry_repo")
    async def registry_repo(registered_app_id: int, archive: bool = True) -> dict:
        return {"repo": "acme/hello"}

    @activity.defn(name="astrolift.capability_deprovision.identity_role")
    async def identity_role(registered_app_id: int) -> dict:
        return {"role": "astrolift-acme-hello"}

    @activity.defn(name="astrolift.app.list_secret_targets")
    async def list_secret_targets(registered_app_id: int) -> list[dict]:
        return [{"ref_id": 21, "tenant_cluster_id": 31, "bundle_slug": "api"}]

    @activity.defn(name="astrolift.secret_rotation.delete_from_cluster")
    async def delete_from_cluster(target: dict) -> dict:
        return {"cluster_slug": "dev-cluster", "errors": []}

    @activity.defn(name="astrolift.app.revoke_secret_bundle_refs")
    async def revoke_refs(registered_app_id: int) -> int:
        return 1

    @activity.defn(name="astrolift.app.delete_source_webhook")
    async def delete_source_webhook(registered_app_id: int) -> dict:
        return {"status": "app_install", "detail": "delivered by the App", "hook_id": ""}

    @activity.defn(name="astrolift.app.revoke_deploy_tokens")
    async def revoke_deploy_tokens(registered_app_id: int) -> int:
        return 1

    @activity.defn(name="astrolift.app.soft_delete_records")
    async def soft_delete_records(registered_app_id: int) -> dict:
        return {"registered_app": 1}

    @activity.defn(name="astrolift.app.mark_deregistered")
    async def mark_deregistered(registered_app_id: int) -> None:
        return None

    @activity.defn(name="astrolift.app.remove_ci_workflow")
    async def remove_ci_workflow_activity(registered_app_id: int) -> dict:
        return remove_ci_workflow(registered_app_id)

    return [
        mark_tearing_down,
        abort_in_flight_deploys,
        delete_namespaces,
        list_managed_service_ids,
        deprovision,
        finalize,
        delete_static_dns,
        registry_repo,
        identity_role,
        list_secret_targets,
        delete_from_cluster,
        revoke_refs,
        delete_source_webhook,
        revoke_deploy_tokens,
        soft_delete_records,
        mark_deregistered,
        remove_ci_workflow_activity,
    ]


async def _deregister(temporal_env, remove_ci_workflow: Callable[[int], dict], workflow_id: str):
    worker = Worker(
        temporal_env.client,
        task_queue=TASK_QUEUE,
        workflows=[DeregisterAppWorkflow],
        activities=_fake_activities(remove_ci_workflow),
        # The runner production uses; a determinism violation fails the run
        # at once instead of retrying the workflow task until the timeout.
        workflow_runner=SandboxedWorkflowRunner(),
        workflow_failure_exception_types=[NondeterminismError],
    )
    async with worker:
        handle = await temporal_env.client.start_workflow(
            DeregisterAppWorkflow.run, _INPUT, id=workflow_id, task_queue=TASK_QUEUE
        )
        result = await asyncio.wait_for(handle.result(), timeout=180)
        return result, await handle.fetch_history()


async def test_a_fresh_deregister_removes_the_ci_workflow_last_and_replays(temporal_env):
    removed = {
        "status": "removed",
        "detail": "removed .github/workflows/astrolift-app-abc.yml from acme/hello@main",
        "repo": "acme/hello",
        "branch": "main",
        "removed": [".github/workflows/astrolift-app-abc.yml"],
        "retained": [],
        "pr_url": "",
    }
    calls: list[int] = []

    def remove(registered_app_id: int) -> dict:
        calls.append(registered_app_id)
        return removed

    result, history = await _deregister(temporal_env, remove, "deregister-removes-ci-workflow-2480")

    assert result.ok is True and result.data["still_live_resources"] == []
    assert _scheduled(history) == [*_BEFORE_2480, "astrolift.app.remove_ci_workflow"]
    assert calls == [7]
    assert result.data["teardown"]["ci_workflow"] == {
        "ok": True,
        "detail": removed["detail"],
        "data": removed,
    }
    await _replay(history)


async def test_a_failed_removal_reports_the_activity_message(temporal_env):
    """The worker wraps an activity failure in ``ActivityError``; the step
    keeps the activity's own message, the one naming the file."""
    message = (
        "could not remove the CI workflow from acme/hello@main (HTTP 409); "
        "delete .github/workflows/astrolift-app-abc.yml by hand if present"
    )

    def refuse(registered_app_id: int) -> dict:
        raise RuntimeError(message)

    result, _ = await _deregister(temporal_env, refuse, "deregister-ci-workflow-refused-2480")

    assert result.ok is True and result.data["still_live_resources"] == []
    assert result.data["teardown"]["ci_workflow"] == {"ok": False, "detail": message, "data": None}


async def test_a_deregister_started_before_the_ci_workflow_step_still_replays():
    """A worker on this code must replay a teardown that finished, or was
    past its last step, when it rolled out, without expecting the removal to
    have been scheduled."""
    history = WorkflowHistory.from_json(
        "legacy-deregister-app-before-ci-workflow-removal",
        (FIXTURES / "legacy-deregister-app-before-ci-workflow-removal.json").read_text(),
    )
    assert _scheduled(history) == _BEFORE_2480

    await _replay(history)
