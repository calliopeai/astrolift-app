"""ProvisionManagedServiceWorkflow (#1001).

Provisions a single ``ManagedService`` row's backend resource via the
provider plugin's ``ManagedServiceDriver.provision`` then flips the
row to ``ACTIVE``. The imperative mirror of
``DeprovisionManagedServiceWorkflow``.

Before #1001 the ``provisionManagedService`` mutation created the row
in ``PENDING`` and started no workflow — its docstring referenced a
"workflow loop" that did not exist, so provisioning never ran and the
whole §4.2 managed-service grid was blocked.

Steps:
  1. mark_managed_service_provisioning — flips the row to
     ``PROVISIONING`` so the UI shows the in-flight state.
  2. provision_managed_service — resolves the ``managed:<kind>:<variant>``
     driver and calls ``provision``. Idempotent (drivers probe for an
     existing resource first), so retries on transient cloud-API
     failures are safe. Driver ``ok=False`` raises, honoring the
     RetryPolicy.
  3. finalize_managed_service_provision — persists the backend handle
     and flips the row to ``ACTIVE``. Decoupled from step 2 so a DB
     hiccup can be retried without re-issuing the cloud provision.

On terminal failure the row is flipped to ``FAILED`` with the error on
``status_error`` so the operator sees why.

Workflow id pattern: ``ProvisionManagedServiceWorkflow-<svc-guid>``.
"""

from __future__ import annotations

import re
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import ProvisionManagedServiceInput, WorkflowResult

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        bounce_workloads_bound_to_managed_service,
        check_managed_service_ready,
        finalize_managed_service_provision,
        mark_managed_service_failed,
        mark_managed_service_provisioning,
        provision_managed_service,
    )


_QUICK_TIMEOUT = timedelta(minutes=2)
# Cloud-side provisions vary widely: Redis/S3 are seconds-to-minutes,
# RDS instance creation routinely takes 10-15 minutes. Generous window
# so the operator's view matches the cloud.
_PROVISION_TIMEOUT = timedelta(minutes=25)

_STATUS_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=15),
    maximum_attempts=3,
)
# Cloud-API retry: longer backoff, more attempts. Providers throttle
# and 5xx; back off and retry. Driver.provision is idempotent.
_PROVISION_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=4,
)


def _truncate(message: str, *, limit: int = 4000) -> str:
    if len(message) <= limit:
        return message
    return message[: limit - 3] + "..."


def _cause(exc: BaseException) -> str:
    """The activity's own error; ``str(ActivityError)`` is only "Activity
    task failed" (#1916) -- same unwrap ``DeployPromotedAppWorkflow`` uses."""
    while getattr(exc, "cause", None) is not None:
        exc = exc.cause  # type: ignore[attr-defined]
    return str(exc)


# Driver messages can echo the config they were given, and an operator can
# type a credential into a field the platform's secret-ref convention
# doesn't recognize as one (#1916). Mask anything that looks like a
# credential assignment or a URI with embedded creds before it reaches the
# row or the operator. A password containing an unescaped ``@`` defeats the
# URI pattern -- accepted gap, real connection strings percent-encode it.
_SECRET_LIKE_KEY = r"(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|credential|private[_-]?key)"
_SECRET_ASSIGNMENT_RE = re.compile(
    rf"(?i)(\b{_SECRET_LIKE_KEY}\b[\"']?\s*[:=]\s*)([\"']?)([^\s,\"'&]+)\2",
)
_URI_CREDENTIAL_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/:@]+:)([^\s/@]+)(@)")


def _redact(message: str) -> str:
    redacted = _SECRET_ASSIGNMENT_RE.sub(r"\1\2***redacted***\2", message)
    return _URI_CREDENTIAL_RE.sub(r"\1***redacted***\3", redacted)


@workflow.defn(name="ProvisionManagedServiceWorkflow")
class ProvisionManagedServiceWorkflow:
    @workflow.run
    async def run(self, input: ProvisionManagedServiceInput) -> WorkflowResult:
        svc_id = input.managed_service_id

        await workflow.execute_activity(
            mark_managed_service_provisioning,
            svc_id,
            start_to_close_timeout=_QUICK_TIMEOUT,
            retry_policy=_STATUS_RETRY,
        )

        try:
            result = await workflow.execute_activity(
                provision_managed_service,
                svc_id,
                start_to_close_timeout=_PROVISION_TIMEOUT,
                retry_policy=_PROVISION_RETRY,
            )
        except Exception as exc:  # noqa: BLE001 — surface to operator
            # #1916: str(exc) on the ActivityError Temporal raises here is
            # the generic "Activity task failed" -- the driver's own reason
            # lives on the innermost cause.
            message = _truncate(f"provision_managed_service failed: {_redact(_cause(exc))}")
            await workflow.execute_activity(
                mark_managed_service_failed,
                args=[svc_id, message],
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_STATUS_RETRY,
            )
            return WorkflowResult(ok=False, message=message)

        handle = result.get("handle", "") if isinstance(result, dict) else ""
        ready = bool(result.get("ready")) if isinstance(result, dict) else False

        # Wait until the backing resource is actually ready before finalize
        # materializes the connection bindings — RDS/ElastiCache report
        # `provisioning` until the endpoint exists, so binding() at finalize
        # would otherwise capture an empty host (#1009). S3 is `available`
        # immediately, so this is a no-op for it. Bounded (~20 min) timer poll
        # rather than a long blocking activity (avoids the #1004 trap).
        #
        # Capability-only services (Bedrock on-demand model access) provision
        # no cloud resource and report `ready=True`; there is no backend
        # `state` to poll, so skip the wait entirely. Polling status() for
        # them false-failed with "backend not ready: state=deprovisioned"
        # because the driver's record is per-instance and absent in the
        # fresh check-activity driver (#1038).
        if handle and not ready:
            for _ in range(60):
                state = await workflow.execute_activity(
                    check_managed_service_ready,
                    args=[svc_id, handle],
                    start_to_close_timeout=_QUICK_TIMEOUT,
                    retry_policy=_STATUS_RETRY,
                )
                if state == "available":
                    break
                if state in ("error", "deprovisioned"):
                    msg = f"managed service backend not ready: state={state}"
                    await workflow.execute_activity(
                        mark_managed_service_failed,
                        args=[svc_id, msg],
                        start_to_close_timeout=_QUICK_TIMEOUT,
                        retry_policy=_STATUS_RETRY,
                    )
                    return WorkflowResult(ok=False, message=msg)
                await workflow.sleep(timedelta(seconds=20))
            else:
                msg = "managed service backend timed out waiting for available state"
                await workflow.execute_activity(
                    mark_managed_service_failed,
                    args=[svc_id, msg],
                    start_to_close_timeout=_QUICK_TIMEOUT,
                    retry_policy=_STATUS_RETRY,
                )
                return WorkflowResult(ok=False, message=msg)

        rebound = await workflow.execute_activity(
            finalize_managed_service_provision,
            args=[svc_id, handle],
            start_to_close_timeout=_QUICK_TIMEOUT,
            retry_policy=_STATUS_RETRY,
        )

        # Spec §4.7 step 5: workloads already running in the environments this
        # service is bound to mount the connection envelope as a Secret and
        # read it once at start, so a brand-new binding never reaches them
        # without a restart. Best-effort inside the activity, so a cluster that
        # cannot be patched does not fail an otherwise-good provision.
        await workflow.execute_activity(
            bounce_workloads_bound_to_managed_service,
            args=[svc_id, rebound or []],
            start_to_close_timeout=_QUICK_TIMEOUT,
            retry_policy=_STATUS_RETRY,
        )

        driver_message = result.get("message", "") if isinstance(result, dict) else ""
        message = "managed service provisioned"
        if driver_message:
            message = f"{message}; {driver_message}"
        return WorkflowResult(ok=True, message=message)
