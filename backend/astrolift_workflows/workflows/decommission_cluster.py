"""DecommissionClusterWorkflow — lift platform RBAC + (optionally) delete
the cluster's cloud infrastructure + flip TenantCluster to ``decommissioned``.

Inverse of ``BringClusterIntoManagementWorkflow``. Two-stage destructive
gradient gated by ``input.delete_cloud_infra``:

  delete_cloud_infra=False (default — historical decommission behavior):
    1. ensure_cluster_drained — refuse if any active AppEnvironment is bound
    2. mark_decommissioning   — UI shows the in-flight state
    3. remove_platform_rbac   — delete astrolift-system namespace, which
                                cascades the four-manifest RBAC bundle
    4. mark_decommissioned    — terminal state; the underlying cluster
                                keeps running, just no longer platform-managed

  delete_cloud_infra=True (#337 — destructive cluster delete):
    1. ensure_cluster_drained
    2. mark_decommissioning
    3. remove_platform_rbac (best-effort — apiserver may go away mid-flight)
    4. teardown_cluster_infra — driver.teardown_cluster deletes the
                                 EKS / GKE / AKS managed cluster + node
                                 pools / Fargate profiles. Bare-metal
                                 returns a no-op + operator-facing message.
    5. mark_decommissioned

Any failure short-circuits to ``mark_error``. Workflow id pattern:
``DecommissionClusterWorkflow-<cluster-guid>``.

Temporal retry posture: AWS / GCP / Azure API calls get the standard
exponential-backoff RetryPolicy (3 attempts, 2s → 15s). The
teardown_cluster_infra activity itself is idempotent — re-running on a
half-deleted cluster reaches the same terminal state.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import DecommissionClusterInput, WorkflowResult

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        ensure_cluster_drained,
        mark_decommissioned,
        mark_decommissioning,
        mark_error,
        remove_platform_rbac,
        teardown_cluster_infra,
    )


_QUICK_TIMEOUT = timedelta(minutes=2)
_DELETE_TIMEOUT = timedelta(minutes=5)
# Cloud-side cluster delete is asynchronous on AWS / GCP / Azure — the
# driver submits the delete and returns, but full deletion completes
# in 5-10 min. Activity timeout is generous so the operator's view of
# "torn down" matches what the cloud actually reports.
_TEARDOWN_TIMEOUT = timedelta(minutes=15)

_MARK_ERROR_RETRY = RetryPolicy(maximum_attempts=1)
_STANDARD_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=15),
    maximum_attempts=3,
)
# Cloud-API teardown gets a longer retry window — AWS / GCP / Azure
# can throttle or 5xx; back off and try again.
_TEARDOWN_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=4,
)
# ensure_cluster_drained is a single read — if it says "still bound" the
# state isn't going to change by retrying. One attempt only.
_DRAIN_RETRY = RetryPolicy(maximum_attempts=1)


def _truncate(message: str, *, limit: int = 4000) -> str:
    if len(message) <= limit:
        return message
    return message[: limit - 3] + "..."


@workflow.defn(name="DecommissionClusterWorkflow")
class DecommissionClusterWorkflow:
    @workflow.run
    async def run(self, input: DecommissionClusterInput) -> WorkflowResult:
        cluster_id = input.cluster_id
        delete_cloud_infra = bool(input.delete_cloud_infra)

        # Step 1 — drain check. Raises into the error path with a
        # bound-count message; we don't proceed to mark_decommissioning
        # until the cluster is empty.
        try:
            await workflow.execute_activity(
                ensure_cluster_drained,
                cluster_id,
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_DRAIN_RETRY,
            )
        except Exception as exc:  # noqa: BLE001 — surface to operator
            message = _truncate(f"decommission refused: {exc}")
            await workflow.execute_activity(
                mark_error,
                args=[cluster_id, message],
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_MARK_ERROR_RETRY,
            )
            return WorkflowResult(ok=False, message=message)

        # Step 2 — flip to decommissioning (UI spinner).
        await workflow.execute_activity(
            mark_decommissioning,
            cluster_id,
            start_to_close_timeout=_QUICK_TIMEOUT,
            retry_policy=_STANDARD_RETRY,
        )

        # Step 3 — remove platform RBAC. When delete_cloud_infra=True
        # we still try this BEFORE the cloud-side delete: the
        # apiserver is still reachable at this point and clean RBAC
        # removal leaves a tidy audit log even if the cluster delete
        # follows. If RBAC removal fails AND we're about to delete the
        # cluster anyway, treat it as a soft failure (log + continue);
        # the cluster's going away regardless.
        rbac_message = ""
        try:
            await workflow.execute_activity(
                remove_platform_rbac,
                cluster_id,
                start_to_close_timeout=_DELETE_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
        except Exception as exc:  # noqa: BLE001 — surface to operator
            rbac_message = f"remove_platform_rbac failed: {exc}"
            if not delete_cloud_infra:
                # Without the destructive follow-up, RBAC failure is the
                # whole story — flip to error and stop.
                message = _truncate(rbac_message)
                await workflow.execute_activity(
                    mark_error,
                    args=[cluster_id, message],
                    start_to_close_timeout=_QUICK_TIMEOUT,
                    retry_policy=_MARK_ERROR_RETRY,
                )
                return WorkflowResult(ok=False, message=message)
            # Else: cluster's getting deleted; carry on with a note.

        # Step 4 — destructive cloud-side delete. Gated on the explicit
        # ``delete_cloud_infra`` flag from the operator. When False this
        # is a no-op pass-through for symmetry — the historical
        # decommission flow remains unchanged.
        teardown_message = ""
        if delete_cloud_infra:
            try:
                report = await workflow.execute_activity(
                    teardown_cluster_infra,
                    args=[cluster_id, True],
                    start_to_close_timeout=_TEARDOWN_TIMEOUT,
                    retry_policy=_TEARDOWN_RETRY,
                )
            except Exception as exc:  # noqa: BLE001 — surface to operator
                message = _truncate(f"teardown_cluster_infra failed: {exc}")
                await workflow.execute_activity(
                    mark_error,
                    args=[cluster_id, message],
                    start_to_close_timeout=_QUICK_TIMEOUT,
                    retry_policy=_MARK_ERROR_RETRY,
                )
                return WorkflowResult(ok=False, message=message)
            deleted = report.get("deleted", []) if isinstance(report, dict) else []
            teardown_message = (
                f"teardown deleted {len(deleted)} cloud resource(s)"
                if deleted
                else "teardown completed (nothing to delete on this provider)"
            )

        # Step 5 — terminal state.
        await workflow.execute_activity(
            mark_decommissioned,
            cluster_id,
            start_to_close_timeout=_QUICK_TIMEOUT,
            retry_policy=_STANDARD_RETRY,
        )
        result_message = "cluster decommissioned"
        if teardown_message:
            result_message = f"{result_message}; {teardown_message}"
        if rbac_message:
            result_message = f"{result_message} (note: {rbac_message})"
        return WorkflowResult(ok=True, message=result_message)
