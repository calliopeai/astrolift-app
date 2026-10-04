"""Reconcile app IAM/bindings for an existing model; never allocate/delete it."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import SharedModelReconcileInput, WorkflowResult

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.native_model_connections import (
        apply_native_model_connection,
        fail_native_model_connection,
        finish_native_model_connection,
    )

_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2), maximum_interval=timedelta(seconds=20), maximum_attempts=3
)


@workflow.defn(name="NativeModelConnectionReconcileWorkflow")
class NativeModelConnectionReconcileWorkflow:
    @workflow.run
    async def run(self, input: SharedModelReconcileInput) -> WorkflowResult:
        try:
            if input.action != "apply" or input.delete_data:
                raise ValueError("Native connections do not own model deletion.")
            apply_state = await workflow.execute_activity(
                apply_native_model_connection,
                args=[input.managed_service_id, input.revision],
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=_RETRY,
            )
            for _ in range(60):
                if await workflow.execute_activity(
                    finish_native_model_connection,
                    args=[input.managed_service_id, input.revision],
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=_RETRY,
                ):
                    return WorkflowResult(
                        ok=True,
                        message=(
                            "Requested removal was observed; inspect retained-source admission separately."
                            if apply_state == "cleanup_pending_source_unavailable"
                            else "Native app configuration was observed; invocation access remains unverified."
                        ),
                    )
                await workflow.sleep(timedelta(seconds=10))
            raise RuntimeError("Native app configuration observation timed out.")
        except Exception:
            await workflow.execute_activity(
                fail_native_model_connection,
                args=[input.managed_service_id, input.revision],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=_RETRY,
            )
            return WorkflowResult(
                ok=False,
                message="Native connection reconciliation failed; inspect current configuration before retrying.",
            )
