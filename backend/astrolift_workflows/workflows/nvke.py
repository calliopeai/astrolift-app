"""Retry observation and durable attempts without replaying accepted mutations."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.nvke import nvke_operation_attempt, nvke_outbox_tick
    from astrolift_workflows.activity_heartbeat import HEARTBEAT_TIMEOUT


@workflow.defn(name="NvkeOperationWorkflow")
class NvkeOperationWorkflow:
    @workflow.run
    async def run(self, operation_id: int, generation: int) -> str:
        while True:
            status = await workflow.execute_activity(
                nvke_operation_attempt,
                args=[operation_id, generation],
                start_to_close_timeout=timedelta(minutes=20),
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            if status not in {"queued", "running", "awaiting_ready", "reconciling", "pending"}:
                return status
            await workflow.sleep(timedelta(seconds=30))


@workflow.defn(name="NvkeOutboxTickWorkflow")
class NvkeOutboxTickWorkflow:
    @workflow.run
    async def run(self) -> int:
        return await workflow.execute_activity(
            nvke_outbox_tick,
            start_to_close_timeout=timedelta(minutes=10),
            heartbeat_timeout=HEARTBEAT_TIMEOUT,
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
