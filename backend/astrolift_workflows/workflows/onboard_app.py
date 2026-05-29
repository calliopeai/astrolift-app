"""
OnboardAppWorkflow — bring a registered app to provisioning_status=ready.

Mirrors specs/06 §4.1. Each step is a single activity call; activity
implementations live in ``astrolift_workflows.activities``.

Idempotent by construction: each activity is idempotent and the
workflow id is ``OnboardAppWorkflow-<app-guid>`` so re-enqueues are
no-ops while a workflow is already in flight.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from astrolift_workflows.inputs import OnboardAppInput, WorkflowResult


with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        mark_app_provisioning,
        mark_app_ready,
        provision_managed_services_initial,
        provision_namespace,
        provision_registry_repo,
    )


_ACTIVITY_TIMEOUT = timedelta(minutes=10)


@workflow.defn(name="OnboardAppWorkflow")
class OnboardAppWorkflow:
    @workflow.run
    async def run(self, input: OnboardAppInput) -> WorkflowResult:
        await workflow.execute_activity(
            mark_app_provisioning,
            input.registered_app_id,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
        )
        await workflow.execute_activity(
            provision_registry_repo,
            input.registered_app_id,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
        )
        await workflow.execute_activity(
            provision_namespace,
            input.registered_app_id,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
        )
        await workflow.execute_activity(
            provision_managed_services_initial,
            args=[input.registered_app_id, 0],  # 0 = no explicit env id
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
        )
        await workflow.execute_activity(
            mark_app_ready,
            input.registered_app_id,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
        )
        return WorkflowResult(ok=True, message="onboarded")
