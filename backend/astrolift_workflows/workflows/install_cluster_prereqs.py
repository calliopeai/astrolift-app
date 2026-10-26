"""InstallClusterPrereqsWorkflow (#66).

Single-activity workflow that applies the operator's bootstrap-recipe
selections to the cluster. Driver-recipe-aware: the activity reads
the driver's ``BootstrapComponent`` set, filters by the operator's
selection, merges per-option overrides, and applies a Flux
``HelmRelease`` per chosen component.

Idempotent — re-run converges. The cluster lifecycle stays whatever
it was (managed / managing); the bootstrap install is an additive
in-cluster operation, not a lifecycle transition. If the apply fails
the workflow surfaces the message; the UI displays it on the cluster
detail page.

Workflow id pattern: ``InstallClusterPrereqsWorkflow-<cluster-guid>``.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from astrolift_workflows.inputs import (
    InstallClusterPrereqsInput,
    WorkflowResult,
)

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        install_cluster_prereqs,
        record_cluster_bootstrap_run,
    )
    from astrolift_workflows.activity_heartbeat import HEARTBEAT_TIMEOUT


# Manifest apply against a Kubernetes cluster typically returns in
# seconds, but Flux reconcile is async — the operator picks up
# rolling-status from the cluster-status tab. We just need enough
# time to apply the HelmRelease CRDs.
_APPLY_TIMEOUT = timedelta(minutes=5)
_READINESS_TIMEOUT = timedelta(minutes=20)
_READINESS_POLL = timedelta(seconds=30)
_READINESS_ERROR_TYPE = "BootstrapReadinessPending"
_APPLY_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)

# Recording the bootstrap run is a fast, non-critical DB write.
_RECORD_TIMEOUT = timedelta(seconds=30)
_RECORD_RETRY = RetryPolicy(maximum_attempts=2)


def _truncate(message: str, *, limit: int = 4000) -> str:
    if len(message) <= limit:
        return message
    return message[: limit - 3] + "..."


def _root_failure(error: BaseException) -> BaseException:
    seen: set[int] = set()
    while id(error) not in seen:
        seen.add(id(error))
        cause = getattr(error, "cause", None) or error.__cause__
        if not isinstance(cause, BaseException):
            break
        error = cause
    return error


async def _apply_until_ready(input: InstallClusterPrereqsInput):
    deadline = workflow.now() + _READINESS_TIMEOUT
    while True:
        remaining = deadline - workflow.now()
        try:
            return await workflow.execute_activity(
                install_cluster_prereqs,
                args=[
                    input.cluster_id,
                    list(input.selected_components),
                    dict(input.option_overrides),
                    input.additive,
                ],
                start_to_close_timeout=min(_APPLY_TIMEOUT, remaining),
                schedule_to_close_timeout=remaining,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=2),
                    maximum_interval=timedelta(seconds=30),
                    maximum_attempts=3,
                    non_retryable_error_types=[_READINESS_ERROR_TYPE],
                ),
            )
        except Exception as error:
            cause = _root_failure(error)
            if not isinstance(cause, ApplicationError) or cause.type != _READINESS_ERROR_TYPE:
                raise
            remaining = deadline - workflow.now()
            if remaining <= _READINESS_POLL:
                raise ApplicationError(
                    f"Bootstrap readiness deadline exceeded: {cause.message}",
                    type="BootstrapReadinessTimeout",
                    non_retryable=True,
                ) from None
            await workflow.sleep(_READINESS_POLL)


@workflow.defn(name="InstallClusterPrereqsWorkflow")
class InstallClusterPrereqsWorkflow:
    @workflow.run
    async def run(
        self,
        input: InstallClusterPrereqsInput,
    ) -> WorkflowResult:
        started_at = workflow.now()
        actor_user_id = input.actor.user_id if input.actor.kind == "user" else None

        readiness_wait = workflow.patched("bootstrap-readiness-wait-v1")
        try:
            if readiness_wait:
                result = await _apply_until_ready(input)
            else:
                result = await workflow.execute_activity(
                    install_cluster_prereqs,
                    args=[
                        input.cluster_id,
                        list(input.selected_components),
                        dict(input.option_overrides),
                        input.additive,
                    ],
                    start_to_close_timeout=_APPLY_TIMEOUT,
                    heartbeat_timeout=HEARTBEAT_TIMEOUT,
                    retry_policy=_APPLY_RETRY,
                )
        except Exception as exc:  # noqa: BLE001
            detail = _root_failure(exc) if readiness_wait else exc
            error_msg = _truncate(f"install_cluster_prereqs failed: {detail}")
            # Best-effort record — don't let a write failure mask the
            # original error.
            try:
                await workflow.execute_activity(
                    record_cluster_bootstrap_run,
                    args=[
                        input.cluster_id,
                        actor_user_id,
                        "failed",
                        [],
                        error_msg,
                        started_at.isoformat(),
                        workflow.now().isoformat(),
                        input.organization_id,
                    ],
                    start_to_close_timeout=_RECORD_TIMEOUT,
                    heartbeat_timeout=HEARTBEAT_TIMEOUT,
                    retry_policy=_RECORD_RETRY,
                )
            except Exception:  # noqa: BLE001
                pass
            return WorkflowResult(ok=False, message=error_msg)

        applied = result.get("applied", []) if isinstance(result, dict) else []
        skipped = result.get("skipped", []) if isinstance(result, dict) else []
        # An additive run records the recipe's whole set (#2130).
        recorded = result.get("recorded", applied) if isinstance(result, dict) else applied

        try:
            await workflow.execute_activity(
                record_cluster_bootstrap_run,
                args=[
                    input.cluster_id,
                    actor_user_id,
                    "succeeded",
                    recorded,
                    "",
                    started_at.isoformat(),
                    workflow.now().isoformat(),
                    input.organization_id,
                ],
                start_to_close_timeout=_RECORD_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=_RECORD_RETRY,
            )
        except Exception:  # noqa: BLE001
            pass

        return WorkflowResult(
            ok=True,
            message=(f"applied {len(applied)} component(s), skipped {len(skipped)}"),
            data=result if isinstance(result, dict) else None,
        )
