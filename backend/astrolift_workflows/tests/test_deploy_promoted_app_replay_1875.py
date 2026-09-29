"""DeployPromotedAppWorkflow's ``workflow.patched`` gate replays clean (#1875).

Mirrors ``test_deploy_workflows_sandboxed_1758.py``'s pattern: run the real
workflow class on the sandboxed runner (production's) so a forbidden import
or a determinism violation fails the test instead of retrying forever, then
replay both a fresh (current-code) history and a legacy one captured before
``record_promoted_app_deployment`` existed.

The legacy fixture was captured by running the workflow exactly as it stood
at cae39272 (this branch's base) -- a single ``deploy_promoted_app``
activity, no recording step -- against the real Temporal test server, then
dumping ``handle.fetch_history()``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer, Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
from temporalio.workflow import NondeterminismError

from astrolift_workflows.inputs import DeployPromotedAppInput
from astrolift_workflows.workflows.dev_environment import DeployPromotedAppWorkflow

TASK_QUEUE = "astrolift-test"
FIXTURES = Path(__file__).parent / "fixtures"


def _scheduled(history: WorkflowHistory) -> list[str]:
    """Activity names, in the order the workflow scheduled them."""
    return [
        event.activity_task_scheduled_event_attributes.activity_type.name.rsplit(".", 1)[-1]
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    ]


async def _replay(history: WorkflowHistory) -> None:
    await Replayer(
        workflows=[DeployPromotedAppWorkflow], workflow_runner=SandboxedWorkflowRunner()
    ).replay_workflow(history)


def _fake_activities(calls: list[str]) -> list:
    @activity.defn(name="astrolift.builder.deploy_promoted_app")
    async def deploy(dev_environment_id: int, storage_class: str) -> dict:
        calls.append("deploy_promoted_app")
        return {"namespace": "acme-shop", "app_url": "https://acme-shop.example.test"}

    @activity.defn(name="astrolift.builder.record_promoted_app_deployment")
    async def record(dev_environment_id: int, storage_class: str) -> dict:
        calls.append("record_promoted_app_deployment")
        return {"workload_id": 1, "deployment_id": 1}

    @activity.defn(name="astrolift.builder.mark_dev_environment_failed")
    async def mark_failed(dev_environment_id: int, error: str) -> None:
        calls.append("mark_dev_environment_failed")

    return [deploy, record, mark_failed]


async def test_a_fresh_deploy_schedules_the_recording_step_and_replays(temporal_env):
    calls: list[str] = []
    worker = Worker(
        temporal_env.client,
        task_queue=TASK_QUEUE,
        workflows=[DeployPromotedAppWorkflow],
        activities=_fake_activities(calls),
        # Worker's default, spelled out: the runner production uses.
        workflow_runner=SandboxedWorkflowRunner(),
        # A sandbox or determinism violation fails the run at once instead
        # of retrying the workflow task until the test is killed.
        workflow_failure_exception_types=[NondeterminismError],
    )
    async with worker:
        handle = await temporal_env.client.start_workflow(
            DeployPromotedAppWorkflow.run,
            DeployPromotedAppInput(dev_environment_id=42, storage_class="gp3"),
            id="deploy-promoted-app-fresh-schedules-recording",
            task_queue=TASK_QUEUE,
        )
        result = await asyncio.wait_for(handle.result(), timeout=180)

    assert result.ok is True
    assert calls == ["deploy_promoted_app", "record_promoted_app_deployment"]
    await _replay(await handle.fetch_history())


async def test_a_run_started_before_the_recording_step_still_replays():
    """Captured from the workflow as it stood before #1875 (a single
    ``deploy_promoted_app`` activity, no recording step). A worker on this
    code must replay a run that was in flight when it rolled out without
    suddenly expecting ``record_promoted_app_deployment`` to have been
    scheduled too."""
    history = WorkflowHistory.from_json(
        "legacy-deploy-promoted-app-no-record",
        (FIXTURES / "legacy-deploy-promoted-app-no-record.json").read_text(),
    )
    scheduled = _scheduled(history)
    assert scheduled == ["deploy_promoted_app"]

    await _replay(history)
