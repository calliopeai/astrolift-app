"""DeprovisionManagedDomainWorkflow.

The inverse of ``ProvisionManagedDomainWorkflow``: deleting a managed
domain used to soft-delete the row and leave the hosted zone and its
wildcard certificate alive in the cloud account. One activity revokes
the certificate (if the row recorded one) and deletes the hosted zone,
records included. Idempotent — a zone already gone is a success.

Workflow id pattern: ``DeprovisionManagedDomainWorkflow-<cluster_id>-<zone>``.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import (
    DeprovisionManagedDomainInput,
    WorkflowResult,
)

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.deprovision_managed_domain import (
        deprovision_managed_domain_resources,
    )


_ACTIVITY_TIMEOUT = timedelta(minutes=5)
_ACTIVITY_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)


@workflow.defn(name="DeprovisionManagedDomainWorkflow")
class DeprovisionManagedDomainWorkflow:
    @workflow.run
    async def run(
        self,
        input: DeprovisionManagedDomainInput,
    ) -> WorkflowResult:
        try:
            result = await workflow.execute_activity(
                deprovision_managed_domain_resources,
                args=[input.cluster_id, input.zone],
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_ACTIVITY_RETRY,
            )
        except Exception as exc:  # noqa: BLE001
            return WorkflowResult(
                ok=False,
                message=f"deprovision_managed_domain_resources failed: {exc}"[:4000],
            )

        data = result if isinstance(result, dict) else {}
        return WorkflowResult(
            ok=True,
            message=f"Zone {input.zone} deprovisioned.",
            data=data,
        )
