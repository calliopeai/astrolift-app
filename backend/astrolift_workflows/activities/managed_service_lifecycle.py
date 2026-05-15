"""Activities for the managed-service lifecycle workflows (#320).

The deprovision path resolves the bound provider plugin's managed-
service driver, calls ``ManagedServiceDriver.deprovision`` with the
two-axis safety flags (``delete_data`` × ``force_destroy``), and
transitions the ``ManagedService`` row through its status states.

All bodies are sync-wrapped via ``sync_to_async`` so Django ORM access
stays off the activity event loop.
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.managed_service")


def _mark_status_sync(managed_service_id: int, status: str) -> None:
    from astrolift_services.models import ManagedService

    svc = ManagedService.all_objects.get(pk=managed_service_id)
    svc.status = status
    svc.save(update_fields=["status", "updated_at", "version"])


@activity.defn(name="astrolift.managed_service.mark_deprovisioning")
async def mark_managed_service_deprovisioning(
    managed_service_id: int,
) -> None:
    from asgiref.sync import sync_to_async

    from astrolift_services.models import ManagedService

    await sync_to_async(_mark_status_sync)(
        managed_service_id,
        ManagedService.Status.DEPROVISIONING,
    )


def _deprovision_sync(
    managed_service_id: int,
    delete_data: bool,
    force_destroy: bool,
) -> dict[str, Any]:
    from astrolift_drivers.registry import DriverNotFound, plugins
    from astrolift_services.models import ManagedService
    from core.cluster_observability import _config_for  # type: ignore[attr-defined]

    svc = ManagedService.all_objects.select_related(
        "app_environment__tenant_cluster__provider_plugin",
    ).get(pk=managed_service_id)
    cluster = svc.app_environment.tenant_cluster
    if cluster is None:
        # Already orphaned — nothing to call into; mark deleted and
        # return a soft-success so the workflow finalizes the row.
        return {
            "ok": True,
            "message": "managed service has no bound cluster — nothing to delete",
            "handle": svc.backend_ref or "",
        }
    plugin_slug = cluster.provider_plugin.slug
    variant = getattr(svc, "variant", "") or ""
    try:
        driver_cls = plugins.get(plugin_slug, f"managed:{svc.kind}:{variant}")
    except DriverNotFound:
        try:
            driver_cls = plugins.get(plugin_slug, f"managed:{svc.kind}:")
        except DriverNotFound as exc:
            raise RuntimeError(
                f"cluster {cluster.slug}: plugin {plugin_slug!r} has no managed-service driver "
                f"for kind={svc.kind!r} variant={variant!r}",
            ) from exc

    cfg = _config_for(plugin_slug, cluster)
    driver = driver_cls(config=cfg)

    from _sdk.managed_service import DeprovisionSpec

    spec = DeprovisionSpec(handle=svc.backend_ref or "")
    result = driver.deprovision(
        spec,
        delete_data=delete_data,
        force_destroy=force_destroy,
    )
    return {
        "ok": bool(getattr(result, "ok", False)),
        "message": str(getattr(result, "message", "")),
        "errors": list(getattr(result, "errors", []) or []),
        "handle": str(getattr(result, "handle", "")),
    }


@activity.defn(name="astrolift.managed_service.deprovision")
async def deprovision_managed_service(
    managed_service_id: int,
    delete_data: bool,
    force_destroy: bool,
) -> dict[str, Any]:
    """Call the driver's ``deprovision`` with the two-axis safety flags.

    Returns the driver's ``DeprovisionResult`` as a plain dict. The
    workflow inspects ``ok`` to decide whether to advance to the
    soft-delete step or fail. Idempotent — re-runs against an
    already-deleted backend resource return ``ok=True`` (drivers
    handle the "already gone" path).
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_deprovision_sync)(
        managed_service_id,
        delete_data,
        force_destroy,
    )
    log.info(
        "deprovision_managed_service result=%s",
        result,
        extra={
            "managed_service_id": managed_service_id,
            "delete_data": delete_data,
            "force_destroy": force_destroy,
        },
    )
    if not result["ok"]:
        raise RuntimeError(
            result["message"] or "driver.deprovision returned ok=False",
        )
    return result


def _finalize_sync(managed_service_id: int) -> None:
    from astrolift_services.models import ManagedService

    svc = ManagedService.all_objects.get(pk=managed_service_id)
    if svc.deleted_at is None:
        svc.soft_delete()


@activity.defn(name="astrolift.managed_service.finalize_deletion")
async def finalize_managed_service_deletion(
    managed_service_id: int,
) -> None:
    """Soft-delete the ``ManagedService`` row.

    Runs after the driver call succeeded. Separates the durable
    "backend resource is gone" step from the platform-row state
    flip so a partial failure of the latter (DB hiccup) can be
    retried independently.
    """
    from asgiref.sync import sync_to_async

    await sync_to_async(_finalize_sync)(managed_service_id)
