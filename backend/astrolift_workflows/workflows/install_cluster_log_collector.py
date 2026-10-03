"""One bounded collector attempt; original request owns durable recovery."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.install_cluster_log_collector import (
        install_cluster_log_collector_attempt,
    )


@workflow.defn(name="InstallClusterLogCollectorWorkflow")
class InstallClusterLogCollectorWorkflow:
    @workflow.run
    async def run(self, operation_id: int, generation: int) -> str:
        return await workflow.execute_activity(
            install_cluster_log_collector_attempt,
            args=[operation_id, generation],
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
