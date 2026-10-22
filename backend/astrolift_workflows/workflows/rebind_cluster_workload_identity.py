"""RebindClusterWorkloadIdentityWorkflow: a re-pointed cluster's role trust, moved (#2472).

``register_tenant_cluster`` starts this when an upsert points a cluster row at
a cluster with another OIDC issuer, the installer's region change among them.
The activity moves the trust of every per-app role minted for the row to the
new issuer and records progress and failures on the request.

Workflow id pattern: ``RebindClusterWorkloadIdentityWorkflow-<request guid>``.
Started without replacing a running execution, so starting it again joins the
run in flight, and a failed run can be started again.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import RebindClusterWorkloadIdentityInput

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import rebind_cluster_workload_identity
    from astrolift_workflows.activities.workload_identity_rebind import REBIND_INCOMPLETE

# One IAM read per app, and a write for each role still on an old issuer.
_TIMEOUT = timedelta(hours=1)
_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
    non_retryable_error_types=[REBIND_INCOMPLETE],
)


@workflow.defn(name="RebindClusterWorkloadIdentityWorkflow")
class RebindClusterWorkloadIdentityWorkflow:
    """Move one re-point request's per-app role trust to its cluster's new issuer."""

    @workflow.run
    async def run(self, input: RebindClusterWorkloadIdentityInput) -> dict[str, Any]:
        return await workflow.execute_activity(
            rebind_cluster_workload_identity,
            input.rebind_id,
            start_to_close_timeout=_TIMEOUT,
            heartbeat_timeout=timedelta(minutes=2),
            retry_policy=_RETRY,
        )
