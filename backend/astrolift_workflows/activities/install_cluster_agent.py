"""No credential material crosses the durable workflow boundary."""

from asgiref.sync import sync_to_async
from temporalio import activity


@activity.defn
async def install_cluster_agent_attempt(install_id: int, generation: int) -> str:
    from astrolift_clusters.agent_install import install_attempt

    info = activity.info()
    return await sync_to_async(install_attempt)(
        install_id,
        generation,
        execution=(info.workflow_id, info.workflow_run_id, info.workflow_type),
    )
