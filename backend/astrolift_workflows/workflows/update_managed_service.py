"""Durable in-place managed-service update workflow."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import UpdateManagedServiceInput, WorkflowResult

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        check_managed_service_ready,
        finalize_managed_service_update,
        mark_managed_service_failed,
        update_managed_service,
    )


_QUICK_TIMEOUT = timedelta(minutes=2)
_UPDATE_TIMEOUT = timedelta(minutes=25)
_STATUS_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=15),
    maximum_attempts=3,
)
_UPDATE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=4,
)


def _truncate(message: str, *, limit: int = 4000) -> str:
    return message if len(message) <= limit else message[: limit - 3] + "..."


@workflow.defn(name="UpdateManagedServiceWorkflow")
class UpdateManagedServiceWorkflow:
    @workflow.run
    async def run(self, input: UpdateManagedServiceInput) -> WorkflowResult:
        svc_id = input.managed_service_id
        try:
            result = await workflow.execute_activity(
                update_managed_service,
                svc_id,
                start_to_close_timeout=_UPDATE_TIMEOUT,
                retry_policy=_UPDATE_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            message = _truncate(f"update_managed_service failed: {exc}")
            await workflow.execute_activity(
                mark_managed_service_failed,
                args=[svc_id, message],
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_STATUS_RETRY,
            )
            return WorkflowResult(ok=False, message=message)

        handle = result.get("handle", "") if isinstance(result, dict) else ""
        if handle:
            for _ in range(60):
                state = await workflow.execute_activity(
                    check_managed_service_ready,
                    args=[svc_id, handle],
                    start_to_close_timeout=_QUICK_TIMEOUT,
                    retry_policy=_STATUS_RETRY,
                )
                if state == "available":
                    break
                if state in {"error", "deprovisioned"}:
                    message = f"managed service backend update failed: state={state}"
                    await workflow.execute_activity(
                        mark_managed_service_failed,
                        args=[svc_id, message],
                        start_to_close_timeout=_QUICK_TIMEOUT,
                        retry_policy=_STATUS_RETRY,
                    )
                    return WorkflowResult(ok=False, message=message)
                await workflow.sleep(timedelta(seconds=20))
            else:
                message = "managed service backend update timed out waiting for available state"
                await workflow.execute_activity(
                    mark_managed_service_failed,
                    args=[svc_id, message],
                    start_to_close_timeout=_QUICK_TIMEOUT,
                    retry_policy=_STATUS_RETRY,
                )
                return WorkflowResult(ok=False, message=message)

        await workflow.execute_activity(
            finalize_managed_service_update,
            args=[svc_id, handle],
            start_to_close_timeout=_QUICK_TIMEOUT,
            retry_policy=_STATUS_RETRY,
        )
        return WorkflowResult(ok=True, message="managed service updated")
