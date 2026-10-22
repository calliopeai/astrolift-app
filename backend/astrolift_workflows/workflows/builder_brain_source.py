"""Build an admitted immutable archive; hosting always requires a fresh HTTP call."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.builder_brain_source import build_brain_source
    from astrolift_workflows.activity_heartbeat import HEARTBEAT_TIMEOUT


@workflow.defn(name="BuilderBrainSourceBuildWorkflow")
class BuilderBrainSourceBuildWorkflow:
    @workflow.run
    async def run(self, intent_id: int) -> dict:
        return await workflow.execute_activity(
            build_brain_source,
            intent_id,
            start_to_close_timeout=timedelta(minutes=35),
            schedule_to_close_timeout=timedelta(minutes=40),
            heartbeat_timeout=HEARTBEAT_TIMEOUT,
            retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2)),
        )
