"""The workflow history receives operation identity and a safe status only."""

from asgiref.sync import sync_to_async
from temporalio import activity

from astrolift_workflows.activity_heartbeat import heartbeat_while


@activity.defn
async def nvke_operation_attempt(operation_id: int, generation: int) -> str:
    from astrolift_clusters.nvke_runtime import attempt

    info = activity.info()
    return await heartbeat_while(
        sync_to_async(attempt, thread_sensitive=False)(
            operation_id,
            generation,
            execution=(info.workflow_id, info.workflow_run_id, info.workflow_type),
        )
    )


@activity.defn
async def nvke_outbox_tick() -> int:
    from astrolift_clusters.nvke_operations import sweep_outbox

    return await heartbeat_while(sync_to_async(sweep_outbox, thread_sensitive=False)(limit=10))
