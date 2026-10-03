"""One bounded installation attempt; metadata-only replay is owned by admission."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.install_cluster_agent import install_cluster_agent_attempt


@workflow.defn(name="InstallClusterAgentWorkflow")
class InstallClusterAgentWorkflow:
    @workflow.run
    async def run(self, install_id: int, generation: int) -> str:
        return await workflow.execute_activity(
            install_cluster_agent_attempt,
            args=[install_id, generation],
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
