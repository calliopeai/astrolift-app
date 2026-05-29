"""NamespaceProvisionWorkflow — provision the app's Kubernetes namespace.

Child workflow of OnboardAppWorkflow. Wrapping provision_namespace in its
own workflow gives namespace provisioning an independent Temporal history
entry so it can be audited and retried without re-running registry setup.

Idempotent — ensure_namespace server-side-applies labels + annotations;
re-runs reconcile rather than fail.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from astrolift_workflows.inputs import OnboardAppInput

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import provision_namespace

_ACTIVITY_TIMEOUT = timedelta(minutes=10)


@workflow.defn(name="NamespaceProvisionWorkflow")
class NamespaceProvisionWorkflow:
    """Ensure the app's Kubernetes namespace exists on its bound cluster."""

    @workflow.run
    async def run(self, input: OnboardAppInput) -> None:
        await workflow.execute_activity(
            provision_namespace,
            input.registered_app_id,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
        )
