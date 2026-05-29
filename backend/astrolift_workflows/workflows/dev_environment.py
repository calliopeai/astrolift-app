"""Temporal workflows for the Calliope App Builder dev-env lifecycle (#767, #768).

``CreateDevEnvironmentWorkflow`` — drives the initial provision (calls
``provision_dev_environment``; on failure persists the error message on
the row so the operator-facing status surface can render it).

``SyncDevEnvironmentFilesWorkflow`` — drives a file-tree update against
a running env (calls ``sync_dev_environment_files``; same failure
handling).

Both are intentionally thin: a single activity + a catch-all that
records the failure. The activity is where idempotency + provider
driver dispatch live.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from astrolift_workflows.inputs import (
    CreateDevEnvironmentInput,
    SyncDevEnvironmentFilesInput,
    WorkflowResult,
)

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.dev_environment import (
        mark_dev_environment_failed,
        provision_dev_environment,
        sync_dev_environment_files,
    )

_ACTIVITY_TIMEOUT = timedelta(minutes=10)


@workflow.defn(name="CreateDevEnvironmentWorkflow")
class CreateDevEnvironmentWorkflow:
    @workflow.run
    async def run(self, input: CreateDevEnvironmentInput) -> WorkflowResult:
        try:
            result = await workflow.execute_activity(
                provision_dev_environment,
                input.dev_environment_id,
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
            )
            return WorkflowResult(ok=True, message="provisioned", data=result)
        except Exception as exc:  # noqa: BLE001 — explicit catch-all
            await workflow.execute_activity(
                mark_dev_environment_failed,
                args=[input.dev_environment_id, str(exc)],
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
            )
            return WorkflowResult(ok=False, message=str(exc))


@workflow.defn(name="SyncDevEnvironmentFilesWorkflow")
class SyncDevEnvironmentFilesWorkflow:
    @workflow.run
    async def run(self, input: SyncDevEnvironmentFilesInput) -> WorkflowResult:
        try:
            await workflow.execute_activity(
                sync_dev_environment_files,
                input.dev_environment_id,
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
            )
            return WorkflowResult(ok=True, message="files synced")
        except Exception as exc:  # noqa: BLE001 — explicit catch-all
            await workflow.execute_activity(
                mark_dev_environment_failed,
                args=[input.dev_environment_id, str(exc)],
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
            )
            return WorkflowResult(ok=False, message=str(exc))
