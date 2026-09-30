"""One accepted shared-model revision; no later intent can overwrite its result."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import SharedModelReconcileInput, WorkflowResult

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.shared_model_reconcile import (
        activate_shared_model_subscriptions,
        apply_shared_model,
        fail_shared_model_reconcile,
        finish_shared_model_reconcile,
        observe_shared_model,
    )

_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2), maximum_interval=timedelta(seconds=30), maximum_attempts=3
)


@workflow.defn(name="SharedModelReconcileWorkflow")
class SharedModelReconcileWorkflow:
    @workflow.run
    async def run(self, input: SharedModelReconcileInput) -> WorkflowResult:
        sid, revision = input.managed_service_id, input.revision
        try:
            await workflow.execute_activity(
                apply_shared_model,
                args=[sid, revision, input.action, input.delete_data],
                start_to_close_timeout=timedelta(minutes=25),
                retry_policy=_RETRY,
            )
            if input.action == "delete":
                return WorkflowResult(ok=True, message="Shared model deletion was confirmed.")
            for _ in range(60):
                state = await workflow.execute_activity(
                    observe_shared_model,
                    args=[sid, revision],
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=_RETRY,
                )
                if state == "ready":
                    break
                if state == "failed":
                    raise RuntimeError("Shared model readiness failed.")
                await workflow.sleep(timedelta(seconds=20))
            else:
                raise RuntimeError("Shared model readiness timed out.")
            await workflow.execute_activity(
                activate_shared_model_subscriptions,
                args=[sid, revision],
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=_RETRY,
            )
            for _ in range(60):
                ready = await workflow.execute_activity(
                    finish_shared_model_reconcile,
                    args=[sid, revision],
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=_RETRY,
                )
                if ready:
                    return WorkflowResult(
                        ok=True, message="Shared model and subscription reconciliation was confirmed."
                    )
                await workflow.sleep(timedelta(seconds=10))
            raise RuntimeError("Subscription activation timed out.")
        except Exception:
            await workflow.execute_activity(
                fail_shared_model_reconcile,
                args=[sid, revision],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=_RETRY,
            )
            return WorkflowResult(
                ok=False, message="Shared model reconciliation failed; retry or operator review is required."
            )
