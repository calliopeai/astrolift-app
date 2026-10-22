"""Activities of ``BrainCreateWorkflow`` (#2424): each runs one idempotent
step of ``astrolift_registry.brain_manage`` against the creation row and
returns ``{"state": ...}``. A refusal is recorded on the row (state
``failed``) rather than raised; only transient host errors raise, so the
activity retries them."""

from __future__ import annotations

from typing import Any

from asgiref.sync import sync_to_async
from temporalio import activity

from astrolift_workflows.activity_heartbeat import heartbeat_while


@activity.defn(name="astrolift.brain_create.repo")
async def brain_create_repo(creation_id: int) -> dict[str, Any]:
    from astrolift_registry.brain_manage import ensure_repo

    return await heartbeat_while(sync_to_async(ensure_repo)(creation_id))


@activity.defn(name="astrolift.brain_create.scaffold")
async def brain_create_scaffold(creation_id: int) -> dict[str, Any]:
    from astrolift_registry.brain_manage import scaffold

    return await heartbeat_while(sync_to_async(scaffold)(creation_id))


@activity.defn(name="astrolift.brain_create.register")
async def brain_create_register(creation_id: int) -> dict[str, Any]:
    from astrolift_registry.brain_manage import register

    return await heartbeat_while(sync_to_async(register)(creation_id))


@activity.defn(name="astrolift.brain_create.deploy")
async def brain_create_deploy(creation_id: int) -> dict[str, Any]:
    from astrolift_registry.brain_manage import deploy

    return await heartbeat_while(sync_to_async(deploy)(creation_id))


@activity.defn(name="astrolift.brain_create.observe")
async def brain_create_observe(creation_id: int) -> dict[str, Any]:
    from astrolift_registry.brain_manage import observe

    return await heartbeat_while(sync_to_async(observe)(creation_id))


@activity.defn(name="astrolift.brain_create.fail")
async def brain_create_fail(creation_id: int, code: str, message: str) -> dict[str, Any]:
    from astrolift_registry.brain_manage import fail

    return await sync_to_async(fail)(creation_id, code, message)
