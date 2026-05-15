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

from astrolift_workflows.inputs import (
    InstallClusterPrereqsInput,
    WorkflowResult,
)

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import install_cluster_prereqs


# Manifest apply against a Kubernetes cluster typically returns in
# seconds, but Flux reconcile is async — the operator picks up
# rolling-status from the cluster-status tab. We just need enough
# time to apply the HelmRelease CRDs.
_APPLY_TIMEOUT = timedelta(minutes=5)
_APPLY_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)


def _truncate(message: str, *, limit: int = 4000) -> str:
    if len(message) <= limit:
        return message
    return message[: limit - 3] + "..."


@workflow.defn(name="InstallClusterPrereqsWorkflow")
class InstallClusterPrereqsWorkflow:
    @workflow.run
    async def run(
        self, input: InstallClusterPrereqsInput,
    ) -> WorkflowResult:
        try:
            result = await workflow.execute_activity(
                install_cluster_prereqs,
                args=[
                    input.cluster_id,
                    list(input.selected_components),
                    dict(input.option_overrides),
                ],
                start_to_close_timeout=_APPLY_TIMEOUT,
                retry_policy=_APPLY_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            return WorkflowResult(
                ok=False,
                message=_truncate(
                    f"install_cluster_prereqs failed: {exc}",
                ),
            )

        applied = (
            result.get("applied", []) if isinstance(result, dict) else []
        )
        skipped = (
            result.get("skipped", []) if isinstance(result, dict) else []
        )
        return WorkflowResult(
            ok=True,
            message=(
                f"applied {len(applied)} component(s), "
                f"skipped {len(skipped)}"
            ),
            data=result if isinstance(result, dict) else None,
        )
