"""Only the durable intent ID enters workflow history; no archive/bearer grant."""

from asgiref.sync import sync_to_async
from temporalio import activity

from astrolift_workflows.activity_heartbeat import heartbeat_while


@activity.defn(name="astrolift.builder.brain_source_build")
async def build_brain_source(intent_id: int) -> dict:
    from astrolift_lifecycle.builder_brain_source import build

    return await heartbeat_while(sync_to_async(build, thread_sensitive=False)(intent_id))
