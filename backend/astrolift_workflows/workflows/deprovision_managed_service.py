"""DeprovisionManagedServiceWorkflow (#320).

Tears down a single ``ManagedService`` row's backend resource via the
provider plugin's ``ManagedServiceDriver.deprovision`` then soft-
deletes the platform row. Inverse of the provision branch in
``OnboardAppWorkflow.provision_managed_services_initial``.

Two-axis safety surface (mirrors the SDK Protocol):

  ``delete_data`` controls what happens to PERSISTENT STATE:
    False (default): graceful — RDS takes a final snapshot, S3 retains
    bucket contents, Redis exports a backup, queues drain rather than
    purge. The artifact survives for later restore.
    True: irreversibly delete state alongside the resource.

  ``force_destroy`` controls SAFETY GUARDS:
    False (default): respect cloud-side deletion-protection flags,
    refuse if a guard trips, error with a clear operator message.
    True: bypass guards (suspend versioning, ignore deletion
    protection, --atomic cleanup).

The four corners of the matrix are documented on the SDK Protocol;
the workflow simply threads both flags into the driver activity. The
UI exposes them as separate explicit checkboxes so destructive paths
can't be triggered accidentally.

Steps:
  1. mark_managed_service_deprovisioning — flips the row to
     ``DEPROVISIONING`` so the UI shows the in-flight state.
  2. deprovision_managed_service — calls into the driver. Idempotent,
     so retries on transient cloud-API failures are safe. Driver
     ``ok=False`` raises, which lets Temporal honor the RetryPolicy.
  3. finalize_managed_service_deletion — soft-delete the platform
     row. Decoupled from step 2 so a DB hiccup at the end can be
     retried without re-issuing the cloud delete.

Workflow id pattern: ``DeprovisionManagedServiceWorkflow-<svc-guid>``.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import DeprovisionManagedServiceInput, WorkflowResult

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        deprovision_managed_service,
        finalize_managed_service_deletion,
        mark_managed_service_deprovisioning,
    )


_QUICK_TIMEOUT = timedelta(minutes=2)
# Cloud-side deletes are mostly fast (seconds) but RDS final-snapshot
# can take 5-15 minutes; bucket-empty on a large bucket can take
# minutes. Generous window so the operator's view matches the cloud.
_DEPROVISION_TIMEOUT = timedelta(minutes=20)

_STATUS_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=15),
    maximum_attempts=3,
)
# Cloud-API retry: longer backoff, more attempts. AWS / GCP / Azure
# throttle and 5xx; back off and try again. Driver is idempotent.
_DEPROVISION_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=30),
    # Multi-resource services (Aurora cluster members, RDS Proxy IAM cleanup)
    # can legitimately take several minutes. Permanent driver failures are
    # raised non-retryable by the activity, so this budget is only consumed by
    # explicit in-progress results and transient cloud errors.
    maximum_attempts=40,
)


def _truncate(message: str, *, limit: int = 4000) -> str:
    if len(message) <= limit:
        return message
    return message[: limit - 3] + "..."


def _reviewed_args(input, arguments):
    # Omitted proof preserves the serialized arguments of legacy histories.
    return arguments if input.reviewed_binding is None else [*arguments, input.reviewed_binding]


@workflow.defn(name="DeprovisionManagedServiceWorkflow")
class DeprovisionManagedServiceWorkflow:
    @workflow.run
    async def run(self, input: DeprovisionManagedServiceInput) -> WorkflowResult:
        svc_id = input.managed_service_id
        delete_data = bool(input.delete_data)
        force_destroy = bool(input.force_destroy)

        await workflow.execute_activity(
            mark_managed_service_deprovisioning,
            args=_reviewed_args(input, [svc_id]),
            start_to_close_timeout=_QUICK_TIMEOUT,
            retry_policy=_STATUS_RETRY,
        )

        try:
            result = await workflow.execute_activity(
                deprovision_managed_service,
                args=_reviewed_args(input, [svc_id, delete_data, force_destroy]),
                start_to_close_timeout=_DEPROVISION_TIMEOUT,
                retry_policy=_DEPROVISION_RETRY,
            )
        except Exception as exc:  # noqa: BLE001 — surface to operator
            return WorkflowResult(
                ok=False,
                message=_truncate(f"deprovision_managed_service failed: {exc}"),
            )

        await workflow.execute_activity(
            finalize_managed_service_deletion,
            args=_reviewed_args(input, [svc_id]),
            start_to_close_timeout=_QUICK_TIMEOUT,
            retry_policy=_STATUS_RETRY,
        )

        driver_message = result.get("message", "") if isinstance(result, dict) else ""
        message = "managed service deprovisioned"
        if driver_message:
            message = f"{message}; {driver_message}"
        return WorkflowResult(ok=True, message=message)
