"""Both teardown workflows revoke native GCP grants before deleting anything (#2278).

Runs the real workflow classes on the runner production uses (Temporal's
sandbox) on the time-skipping test server, against fake activities registered
under the real names, then replays each fresh history. A TearDownAppWorkflow
history captured before the step existed replays too; the DeregisterAppWorkflow
one is ``test_deregister_ci_workflow_replay_2480``'s.

The legacy fixture was captured by running TearDownAppWorkflow exactly as it
stood at 66a406a1e (this branch's base), the fakes below with one managed
service, against the real Temporal test server, dumping ``handle.fetch_history()``
with worker identities normalized to ``1@astrolift-worker``.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

from temporalio import activity, workflow
from temporalio.client import WorkflowHistory
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer, Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
from temporalio.workflow import NondeterminismError

from astrolift_workflows.inputs import Actor, DeprovisionManagedServiceInput, TearDownAppInput, WorkflowResult
from astrolift_workflows.tests.test_deregister_ci_workflow_replay_2480 import (
    _BEFORE_2480,
    _INPUT,
    _fake_activities,
    _scheduled,
)
from astrolift_workflows.workflows.deregister_app import DeregisterAppWorkflow
from astrolift_workflows.workflows.tear_down_app import TearDownAppWorkflow

TASK_QUEUE = "astrolift-test"
FIXTURES = Path(__file__).parent / "fixtures"
REMOVE = "astrolift.app.remove_native_endpoint_identity"
PENDING = "NATIVE_IDENTITY_REMOVAL_PENDING:PRIOR_SEND_UNRESOLVED"
REMOVED = [{"cluster": "4bd5a2b0-6f43-4a7e-9d43-3f7a1c2d9e01", "removed": 3}]
_REMOVED_CI = {"status": "removed", "detail": "removed the CI workflow", "removed": [], "retained": []}


def _outcome(outcome):
    """The removal's result, or the fixed refusal the activity raises."""

    def remove(registered_app_id: int) -> list[dict]:
        if isinstance(outcome, str):
            raise ApplicationError(outcome, non_retryable=True)
        return outcome

    return remove


def _native(outcome):
    @activity.defn(name=REMOVE)
    async def remove_native_endpoint_identity(registered_app_id: int) -> list[dict]:
        return _outcome(outcome)(registered_app_id)

    return remove_native_endpoint_identity


async def _deregister(temporal_env, native, input, workflow_id):
    @activity.defn(name="astrolift.app.expiry_retire_runtime")
    async def retire_runtime(registered_app_id: int) -> list[str]:
        return []

    worker = Worker(
        temporal_env.client,
        task_queue=TASK_QUEUE,
        workflows=[DeregisterAppWorkflow],
        activities=[*_fake_activities(lambda _app_id: _REMOVED_CI, _outcome(native)), retire_runtime],
        # A determinism violation fails the run at once.
        workflow_runner=SandboxedWorkflowRunner(),
        workflow_failure_exception_types=[NondeterminismError],
    )
    async with worker:
        handle = await temporal_env.client.start_workflow(
            DeregisterAppWorkflow.run, input, id=workflow_id, task_queue=TASK_QUEUE
        )
        result = await asyncio.wait_for(handle.result(), timeout=180)
        history = await handle.fetch_history()
    await Replayer(
        workflows=[DeregisterAppWorkflow], workflow_runner=SandboxedWorkflowRunner()
    ).replay_workflow(history)
    return result, history


async def test_deregister_revokes_native_grants_before_any_managed_service_is_deprovisioned(temporal_env):
    result, history = await _deregister(temporal_env, REMOVED, _INPUT, "deregister-native-identity-2278")

    assert result.ok is True and result.data["still_live_resources"] == []
    assert _scheduled(history) == [
        *_BEFORE_2480[:3],
        REMOVE,
        *_BEFORE_2480[3:],
        "astrolift.app.remove_ci_workflow",
    ]
    assert result.data["teardown"]["native_identity"] == {
        "ok": True,
        "detail": "revoked 3 owned grant(s) on 1 cluster(s)",
        "data": REMOVED,
    }


async def test_pending_native_removal_holds_the_managed_services_and_keeps_the_rows(temporal_env):
    result, history = await _deregister(temporal_env, PENDING, _INPUT, "deregister-native-pending-2278")

    assert result.ok is False
    assert result.data["still_live_resources"] == ["native_identity", "managed_services"]
    teardown = result.data["teardown"]
    assert teardown["native_identity"] == {"ok": False, "detail": PENDING, "data": None}
    # The Endpoints whose policies still name the app are not deleted.
    assert teardown["managed_services"]["data"] == [
        {"ok": False, "managed_service_id": 11, "message": "held for the native identity removal"}
    ]
    assert teardown["platform_rows"]["ok"] is False
    scheduled = _scheduled(history)
    assert scheduled == [
        *_BEFORE_2480[:3],
        REMOVE,
        *(
            name
            for name in _BEFORE_2480[3:]
            if name
            not in {
                "astrolift.managed_service.deprovision",
                "astrolift.managed_service.finalize_deletion",
                "astrolift.app.soft_delete_records",
                "astrolift.app.mark_deregistered",
            }
        ),
    ]


async def test_expiry_keeps_its_services_while_the_native_removal_is_pending(temporal_env):
    """The expiry's kept services are not failures; the rows still wait."""
    expiry = dataclasses.replace(_INPUT, keep_managed_services=True, skip_grace=True)
    result, history = await _deregister(
        temporal_env, PENDING, expiry, "deregister-expiry-native-pending-2278"
    )

    assert result.data["still_live_resources"] == ["native_identity"]
    assert result.data["teardown"]["managed_services"]["data"] == [
        {"ok": True, "managed_service_id": 11, "message": "kept"}
    ]
    assert "astrolift.app.soft_delete_records" not in _scheduled(history)


@workflow.defn(name="DeprovisionManagedServiceWorkflow")
class FakeDeprovision:
    @workflow.run
    async def run(self, input: DeprovisionManagedServiceInput) -> WorkflowResult:
        return WorkflowResult(ok=True, message="snapshot retained")


def _tear_down_activities(native):
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

    @activity.defn(name="astrolift.app.revoke_deploy_tokens")
    async def revoke_deploy_tokens(registered_app_id: int) -> int:
        return 1

    @activity.defn(name="astrolift.app.soft_delete_records")
    async def soft_delete_records(registered_app_id: int) -> dict:
        return {"registered_app": 1}

    @activity.defn(name="astrolift.app.mark_deregistered")
    async def mark_deregistered(registered_app_id: int) -> None:
        return None

    return [
        mark_tearing_down,
        abort_in_flight_deploys,
        delete_namespaces,
        list_managed_service_ids,
        revoke_deploy_tokens,
        soft_delete_records,
        mark_deregistered,
        _native(native),
    ]


def _children(history: WorkflowHistory) -> list[str]:
    return [
        event.start_child_workflow_execution_initiated_event_attributes.workflow_type.name
        for event in history.events
        if event.HasField("start_child_workflow_execution_initiated_event_attributes")
    ]


async def _tear_down(temporal_env, native, workflow_id):
    worker = Worker(
        temporal_env.client,
        task_queue=TASK_QUEUE,
        workflows=[TearDownAppWorkflow, FakeDeprovision],
        activities=_tear_down_activities(native),
        workflow_runner=SandboxedWorkflowRunner(),
        workflow_failure_exception_types=[NondeterminismError],
    )
    async with worker:
        handle = await temporal_env.client.start_workflow(
            TearDownAppWorkflow.run,
            TearDownAppInput(registered_app_id=7, actor=Actor(kind="user", display="operator")),
            id=workflow_id,
            task_queue=TASK_QUEUE,
        )
        result = await asyncio.wait_for(handle.result(), timeout=120)
        history = await handle.fetch_history()
    await Replayer(
        workflows=[TearDownAppWorkflow], workflow_runner=SandboxedWorkflowRunner()
    ).replay_workflow(history)
    return result, history


_TEAR_DOWN_BEFORE = [
    "astrolift.app.mark_tearing_down",
    "astrolift.app.abort_in_flight_deploys",
    "astrolift.app.delete_namespaces",
    "astrolift.app.list_managed_service_ids",
    "astrolift.app.revoke_deploy_tokens",
    "astrolift.app.soft_delete_records",
    "astrolift.app.mark_deregistered",
]


async def test_tear_down_revokes_native_grants_before_deprovisioning(temporal_env):
    result, history = await _tear_down(temporal_env, REMOVED, "tear-down-native-identity-2278")

    assert result.ok is True
    assert _scheduled(history) == [*_TEAR_DOWN_BEFORE[:3], REMOVE, *_TEAR_DOWN_BEFORE[3:]]
    assert _children(history) == ["DeprovisionManagedServiceWorkflow"]
    assert result.data["native_identity"] == {"ok": True, "data": REMOVED}


async def test_tear_down_with_a_pending_native_removal_deletes_nothing_further(temporal_env):
    result, history = await _tear_down(temporal_env, PENDING, "tear-down-native-pending-2278")

    assert result.ok is False and PENDING in result.message
    assert _scheduled(history) == [*_TEAR_DOWN_BEFORE[:3], REMOVE, "astrolift.app.revoke_deploy_tokens"]
    assert _children(history) == []
    assert result.data["native_identity"] == {"ok": False, "detail": PENDING}


async def test_a_tear_down_started_before_the_native_step_still_replays():
    history = WorkflowHistory.from_json(
        "legacy-tear-down-app-before-native-identity-removal",
        (FIXTURES / "legacy-tear-down-app-before-native-identity-removal.json").read_text(),
    )
    assert _scheduled(history) == _TEAR_DOWN_BEFORE
    assert _children(history) == ["DeprovisionManagedServiceWorkflow"]

    await Replayer(
        workflows=[TearDownAppWorkflow], workflow_runner=SandboxedWorkflowRunner()
    ).replay_workflow(history)
