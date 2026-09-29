"""Temporal workflows for the Calliope App Builder dev-env lifecycle (#767, #768).

``CreateDevEnvironmentWorkflow`` — drives the initial provision (calls
``provision_dev_environment``; on failure persists the error message on
the row so the operator-facing status surface can render it).

``SyncDevEnvironmentFilesWorkflow`` — drives a file-tree update against
a running env (calls ``sync_dev_environment_files``; same failure
handling).

``DeployPromotedAppWorkflow`` (#1858): serves a promoted app from its own
namespace (calls ``deploy_promoted_app``; same failure handling, recorded
on the dev environment the app was promoted from). Records the runtime as
a Workload + Deployment so the app's own pages, rollback and observability
see it (``record_promoted_app_deployment``, #1875), gated behind
``workflow.patched`` since it is a new step in the workflow's own
sequence, not just a change inside an existing activity.

All are intentionally thin: a single activity + a catch-all that
records the failure. The activity is where idempotency + provider
driver dispatch live.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import (
    CreateDevEnvironmentInput,
    DeployPromotedAppInput,
    SyncDevEnvironmentFilesInput,
    WorkflowResult,
)

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.dev_environment import (
        deploy_promoted_app,
        mark_dev_environment_failed,
        provision_dev_environment,
        record_promoted_app_deployment,
        sync_dev_environment_files,
    )

_ACTIVITY_TIMEOUT = timedelta(minutes=10)
# Bounded, as DeployAppWorkflow's are: under Temporal's default unlimited
# retries a failing activity never raises into the catch-alls below, so the
# failure was never recorded and the retries held a worker thread (#1858).
_ACTIVITY_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)


def _cause(exc: BaseException) -> str:
    """The activity's own error; ``str(ActivityError)`` is only "Activity task failed"."""
    while getattr(exc, "cause", None) is not None:
        exc = exc.cause  # type: ignore[attr-defined]
    return str(exc)


@workflow.defn(name="CreateDevEnvironmentWorkflow")
class CreateDevEnvironmentWorkflow:
    @workflow.run
    async def run(self, input: CreateDevEnvironmentInput) -> WorkflowResult:
        try:
            result = await workflow.execute_activity(
                provision_dev_environment,
                input.dev_environment_id,
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_ACTIVITY_RETRY,
            )
            return WorkflowResult(ok=True, message="provisioned", data=result)
        except Exception as exc:  # noqa: BLE001 — explicit catch-all
            await workflow.execute_activity(
                mark_dev_environment_failed,
                args=[input.dev_environment_id, _cause(exc)],
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
            )
            return WorkflowResult(ok=False, message=_cause(exc))


@workflow.defn(name="SyncDevEnvironmentFilesWorkflow")
class SyncDevEnvironmentFilesWorkflow:
    @workflow.run
    async def run(self, input: SyncDevEnvironmentFilesInput) -> WorkflowResult:
        try:
            await workflow.execute_activity(
                sync_dev_environment_files,
                input.dev_environment_id,
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_ACTIVITY_RETRY,
            )
            return WorkflowResult(ok=True, message="files synced")
        except Exception as exc:  # noqa: BLE001 — explicit catch-all
            await workflow.execute_activity(
                mark_dev_environment_failed,
                args=[input.dev_environment_id, _cause(exc)],
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
            )
            return WorkflowResult(ok=False, message=_cause(exc))


@workflow.defn(name="DeployPromotedAppWorkflow")
class DeployPromotedAppWorkflow:
    @workflow.run
    async def run(self, input: DeployPromotedAppInput) -> WorkflowResult:
        try:
            result = await workflow.execute_activity(
                deploy_promoted_app,
                args=[input.dev_environment_id, input.storage_class],
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_ACTIVITY_RETRY,
            )
            # #1875: record the runtime as a Workload + Deployment so the
            # app pages, rollback and observability see it. Patched so a
            # deploy already in flight when this shipped replays without
            # the extra step.
            if workflow.patched("deploy-promoted-app-records-workload"):
                await workflow.execute_activity(
                    record_promoted_app_deployment,
                    args=[input.dev_environment_id, input.storage_class],
                    start_to_close_timeout=_ACTIVITY_TIMEOUT,
                    retry_policy=_ACTIVITY_RETRY,
                )
            return WorkflowResult(ok=True, message="promoted app deployed", data=result)
        except Exception as exc:  # noqa: BLE001 (explicit catch-all)
            await workflow.execute_activity(
                mark_dev_environment_failed,
                args=[input.dev_environment_id, _cause(exc)],
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
            )
            return WorkflowResult(ok=False, message=_cause(exc))
