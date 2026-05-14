"""DecommissionClusterWorkflow — lift platform RBAC + flip a TenantCluster
to ``decommissioned``.

Inverse of ``BringClusterIntoManagementWorkflow``. Used when an operator
is retiring a cluster (the underlying infra is going away or the cluster
is being moved out of the astrolift fleet). The workflow refuses to
proceed when active app environments are still bound — operators must
migrate or delete those first.

Activity sequence:

  1. ensure_cluster_drained — refuse if any active AppEnvironment is bound
  2. mark_decommissioning   — UI shows the in-flight state
  3. remove_platform_rbac   — delete astrolift-system namespace, which
                              cascades the four-manifest RBAC bundle
  4. mark_decommissioned    — terminal state; cluster excluded from
                              the active-cluster picker going forward

Any failure short-circuits to ``mark_error`` with the failure message
in ``last_management_error`` so the operator can inspect, fix, and
retry. Workflow id pattern:
``DecommissionClusterWorkflow-<cluster-guid>``.
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
    )


_QUICK_TIMEOUT = timedelta(minutes=2)
_DELETE_TIMEOUT = timedelta(minutes=5)

_MARK_ERROR_RETRY = RetryPolicy(maximum_attempts=1)
_STANDARD_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=15),
    maximum_attempts=3,
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

        # Step 3 — actually delete the platform footprint. We tolerate
        # a wider retry window here because the namespace delete races
        # against finalizers on RBAC objects.
        try:
            await workflow.execute_activity(
                remove_platform_rbac,
                cluster_id,
                start_to_close_timeout=_DELETE_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
        except Exception as exc:  # noqa: BLE001 — surface to operator
            message = _truncate(f"remove_platform_rbac failed: {exc}")
            await workflow.execute_activity(
                mark_error,
                args=[cluster_id, message],
                start_to_close_timeout=_QUICK_TIMEOUT,
                retry_policy=_MARK_ERROR_RETRY,
            )
            return WorkflowResult(ok=False, message=message)

        # Step 4 — terminal state.
        await workflow.execute_activity(
            mark_decommissioned,
            cluster_id,
            start_to_close_timeout=_QUICK_TIMEOUT,
            retry_policy=_STANDARD_RETRY,
        )
        return WorkflowResult(ok=True, message="cluster decommissioned")
