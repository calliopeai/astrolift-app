"""ReconcileWorkloadIdentityWorkflow: an app's workload identity, reconciled outside a deploy (#2099).

A deploy reconciles the identity before it renders, so a binding detached
between deploys kept what it granted until the app deployed again. Detaching a
Cloud Operations bundle from an app environment starts this workflow, and its
reconcile takes the bundle's log view grant off the app's identity then.

Workflow id pattern: ``ReconcileWorkloadIdentityWorkflow-<app environment guid>``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import ReconcileWorkloadIdentityInput

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import ensure_workload_identity

# The same budget the deploy gives this activity: IAM writes are a few
# round trips each, and a throttled or 5xx call is retried.
_TIMEOUT = timedelta(minutes=20)
_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)


@workflow.defn(name="ReconcileWorkloadIdentityWorkflow")
class ReconcileWorkloadIdentityWorkflow:
    """Reconcile one app environment's workload identity against its live bindings."""

    @workflow.run
    async def run(self, input: ReconcileWorkloadIdentityInput) -> dict[str, Any]:
        return await workflow.execute_activity(
            ensure_workload_identity,
            args=[input.registered_app_id, input.app_environment_id],
            start_to_close_timeout=_TIMEOUT,
            retry_policy=_RETRY,
        )
