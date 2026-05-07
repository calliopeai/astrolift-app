"""
DeployAppWorkflow — realize one rollout to one (app, env).

Mirrors specs/06 §4.2. Single-flight per (app, env) is enforced by
the workflow id: ``DeployAppWorkflow-<app-guid>-<env-guid>``. A
duplicate-start signal is wired up below so a newer deploy aborts
the in-flight one.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from astrolift_workflows.inputs import DeployAppInput, WorkflowResult


with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        apply_manifests,
        health_check,
        mark_deploying,
        mark_running,
        poll_rollout,
        pre_flight,
        render_manifests,
        update_secrets,
        wait_dns,
    )


_TIMEOUT = timedelta(minutes=15)


@workflow.defn(name="DeployAppWorkflow")
class DeployAppWorkflow:
    def __init__(self) -> None:
        self._abort_requested: bool = False

    @workflow.signal(name="abort")
    def request_abort(self) -> None:
        self._abort_requested = True

    @workflow.run
    async def run(self, input: DeployAppInput) -> WorkflowResult:
        # Resolve / create the deployment row out-of-band before this
        # workflow starts; the workflow operates on its id.
        deployment_id = input.app_environment_id  # placeholder

        await workflow.execute_activity(pre_flight, deployment_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(mark_deploying, deployment_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(render_manifests, deployment_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(apply_manifests, deployment_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(update_secrets, deployment_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(wait_dns, deployment_id, start_to_close_timeout=_TIMEOUT)

        if self._abort_requested:
            return WorkflowResult(ok=False, message="aborted by signal")

        await workflow.execute_activity(poll_rollout, deployment_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(health_check, deployment_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(mark_running, deployment_id, start_to_close_timeout=_TIMEOUT)

        return WorkflowResult(ok=True, message="deployed")
