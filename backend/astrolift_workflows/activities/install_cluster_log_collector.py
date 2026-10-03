"""Only operation identity/generation crosses the durable workflow boundary."""

from asgiref.sync import sync_to_async
from temporalio import activity


@activity.defn
async def install_cluster_log_collector_attempt(operation_id: int, generation: int) -> str:
    from astrolift_clusters.log_collector_runtime import install_attempt

    info = activity.info()
    return await sync_to_async(install_attempt)(
        operation_id,
        generation,
        execution=(info.workflow_id, info.workflow_run_id, info.workflow_type),
    )
