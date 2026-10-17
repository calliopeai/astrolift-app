"""Activities of ``AppExpiryWorkflow`` (#2433): thin wrappers over
``astrolift_registry.expiry``, which holds every decision."""

from __future__ import annotations

from typing import Any

from asgiref.sync import sync_to_async
from temporalio import activity


@activity.defn(name="astrolift.app.expiry_next_step")
async def app_expiry_next_step(registered_app_id: int) -> dict[str, Any]:
    from astrolift_registry.expiry import next_step

    return await sync_to_async(next_step)(registered_app_id)


@activity.defn(name="astrolift.app.expiry_fire")
async def fire_app_expiry(registered_app_id: int) -> bool:
    from astrolift_registry.expiry import fire

    return await sync_to_async(fire)(registered_app_id)


@activity.defn(name="astrolift.app.expiry_record_teardown")
async def record_app_expiry_teardown(registered_app_id: int, teardown: dict[str, Any] | None) -> None:
    from astrolift_registry.expiry import record_teardown

    await sync_to_async(record_teardown)(registered_app_id, teardown)


@activity.defn(name="astrolift.app.expiry_record_purge")
async def record_app_expiry_purge(registered_app_id: int, results: list[dict[str, Any]]) -> None:
    from astrolift_registry.expiry import record_purge

    await sync_to_async(record_purge)(registered_app_id, results)


@activity.defn(name="astrolift.app.expiry_sweep")
async def sweep_app_expiry() -> int:
    from astrolift_registry.expiry import sweep

    return await sync_to_async(sweep)()
