"""The provision workflow's failure-event step replays clean (#2608).

``_fail`` now schedules ``emit_provision_failed_event`` after its two
bookkeeping activities, behind ``workflow.patched``. Runs the real workflow
class on the runner production uses (Temporal's sandbox) on the time-skipping
test server, against fake activities, then replays the fresh history and one
captured before the step existed.

The legacy fixture was captured by running the workflow exactly as it stood at
273132634 (this branch's base): a provision the driver refused, against the
real Temporal test server, dumping ``handle.fetch_history()``. Worker
identities were then normalized to ``1@astrolift-worker`` and the host paths
in the activity failure's stack trace rewritten to the image's.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowHandle, WorkflowHistory
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer, Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
from temporalio.workflow import NondeterminismError

from astrolift_workflows.inputs import Actor, ProvisionManagedServiceInput, WorkflowResult
from astrolift_workflows.workflows.provision_managed_service import ProvisionManagedServiceWorkflow

TASK_QUEUE = "astrolift-test"
FIXTURES = Path(__file__).parent / "fixtures"

# What a refused provision scheduled before #2608, in order.
_BEFORE_2608 = [
    "astrolift.managed_service.mark_provisioning",
    "astrolift.managed_service.provision",
    "astrolift.managed_service.mark_failed",
    "astrolift.managed_service.record_failed_step",
]


def _scheduled(history: WorkflowHistory) -> list[str]:
    return [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    ]


async def _replay(history: WorkflowHistory) -> None:
    await Replayer(
        workflows=[ProvisionManagedServiceWorkflow], workflow_runner=SandboxedWorkflowRunner()
    ).replay_workflow(history)


def _fake_activities(events: list[list], *, event_write_fails: bool) -> list:
    @activity.defn(name="astrolift.managed_service.mark_provisioning")
    async def mark_provisioning(managed_service_id: int) -> dict:
        return {"failed_step": "", "backend_ref": ""}

    @activity.defn(name="astrolift.managed_service.provision")
    async def provision(managed_service_id: int) -> dict:
        raise ApplicationError("capacity exhausted", non_retryable=True)

    @activity.defn(name="astrolift.managed_service.mark_failed")
    async def mark_failed(managed_service_id: int, error: str) -> None:
        return None

    @activity.defn(name="astrolift.managed_service.record_failed_step")
    async def record_failed_step(managed_service_id: int, failed_step: str) -> None:
        return None

    @activity.defn(name="astrolift.managed_service.emit_provision_failed_event")
    async def emit_provision_failed_event(managed_service_id: int, failed_step: str, error: str) -> None:
        events.append([managed_service_id, failed_step, error])
        if event_write_fails:
            raise ApplicationError("events backend unavailable", non_retryable=True)

    return [mark_provisioning, provision, mark_failed, record_failed_step, emit_provision_failed_event]


async def _refused_provision(
    temporal_env, events: list[list], *, workflow_id: str, event_write_fails: bool = False
) -> tuple[WorkflowHandle, WorkflowResult]:
    worker = Worker(
        temporal_env.client,
        task_queue=TASK_QUEUE,
        workflows=[ProvisionManagedServiceWorkflow],
        activities=_fake_activities(events, event_write_fails=event_write_fails),
        # Worker's default, spelled out: the runner production uses.
        workflow_runner=SandboxedWorkflowRunner(),
        # A sandbox or determinism violation fails the run at once instead
        # of retrying the workflow task until the test is killed.
        workflow_failure_exception_types=[NondeterminismError],
    )
    async with worker:
        handle = await temporal_env.client.start_workflow(
            ProvisionManagedServiceWorkflow.run,
            ProvisionManagedServiceInput(managed_service_id=7, actor=Actor(kind="system")),
            id=workflow_id,
            task_queue=TASK_QUEUE,
        )
        result = await asyncio.wait_for(handle.result(), timeout=120)
    return handle, result


async def test_a_fresh_refused_provision_records_the_event_and_replays(temporal_env):
    events: list[list] = []
    handle, result = await _refused_provision(
        temporal_env, events, workflow_id="provision-failure-event-2608"
    )

    assert result.ok is False
    assert events == [[7, "provision", result.message]]
    history = await handle.fetch_history()
    assert _scheduled(history) == [*_BEFORE_2608, "astrolift.managed_service.emit_provision_failed_event"]
    await _replay(history)


async def test_a_lost_event_write_keeps_the_recorded_failure_as_the_result(temporal_env):
    events: list[list] = []
    handle, result = await _refused_provision(
        temporal_env, events, workflow_id="provision-failure-event-lost-2608", event_write_fails=True
    )

    assert len(events) == 1
    assert result.ok is False
    assert result.message == "provision_managed_service failed: capacity exhausted"
    await _replay(await handle.fetch_history())


async def test_a_refused_provision_recorded_before_the_event_step_still_replays():
    """A worker on this code must replay a run recorded by the previous one
    without expecting the event activity to have been scheduled too."""
    history = WorkflowHistory.from_json(
        "legacy-provision-managed-service-failed",
        (FIXTURES / "legacy-provision-managed-service-failed.json").read_text(),
    )
    assert _scheduled(history) == _BEFORE_2608

    await _replay(history)
