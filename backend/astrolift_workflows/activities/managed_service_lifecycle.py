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
    from core.cluster_observability import managed_config_for

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
    if not svc.backend_ref:
        # No provider-side resource was ever created (provision failed or
        # never finalized) — nothing to delete. Soft-success so teardown
        # completes instead of stalling on a non-existent resource.
        return {
            "ok": True,
            "message": "managed service has no backend resource — nothing to delete",
            "handle": "",
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

    cfg = managed_config_for(plugin_slug, cluster, kind=svc.kind, variant=variant)
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


# ---- provision (#1001) ---------------------------------------------
# The mirror of the deprovision path: the provision mutation creates
# the row in PENDING and fires ProvisionManagedServiceWorkflow, which
# drives these activities (mark_provisioning -> provision -> finalize).
# Before #1001 the mutation started no workflow and the row sat PENDING
# forever — provisioning never ran.


@activity.defn(name="astrolift.managed_service.mark_provisioning")
async def mark_managed_service_provisioning(
    managed_service_id: int,
) -> None:
    from asgiref.sync import sync_to_async

    from astrolift_services.models import ManagedService

    await sync_to_async(_mark_status_sync)(
        managed_service_id,
        ManagedService.Status.PROVISIONING,
    )


def _provision_sync(managed_service_id: int) -> dict[str, Any]:
    from astrolift_drivers.registry import DriverNotFound, plugins
    from astrolift_services.models import ManagedService
    from core.cluster_observability import managed_config_for

    svc = ManagedService.all_objects.select_related(
        "registered_app__organization",
        "app_environment__tenant_cluster__provider_plugin",
    ).get(pk=managed_service_id)
    env = svc.app_environment
    cluster = env.tenant_cluster
    if cluster is None:
        raise RuntimeError(
            f"managed service {svc.pk} env has no tenant_cluster bound",
        )
    plugin_slug = cluster.provider_plugin.slug
    variant = getattr(svc, "variant", "") or ""
    try:
        driver_cls = plugins.get(plugin_slug, f"managed:{svc.kind}:{variant}")
    except DriverNotFound:
        # Some plugins register the empty-variant default
        # (``managed:postgres:`` rather than ``managed:postgres:rds``).
        try:
            driver_cls = plugins.get(plugin_slug, f"managed:{svc.kind}:")
        except DriverNotFound as exc:
            raise RuntimeError(
                f"cluster {cluster.slug}: plugin {plugin_slug!r} has no managed-service driver "
                f"for kind={svc.kind!r} variant={variant!r}",
            ) from exc

    cfg = managed_config_for(plugin_slug, cluster, kind=svc.kind, variant=variant)
    driver = driver_cls(config=cfg)

    from _sdk.managed_service import ProvisionSpec

    app = svc.registered_app
    org = app.organization
    svc_config = dict(svc.config or {})
    spec = ProvisionSpec(
        organization_id=str(getattr(org, "guid", "") or ""),
        organization_slug=getattr(org, "slug", "") or "",
        app_id=str(getattr(app, "guid", "") or ""),
        app_slug=app.slug,
        environment_id=str(getattr(env, "guid", "") or ""),
        environment_name=env.name,
        tenant_cluster_id=str(getattr(cluster, "guid", "") or ""),
        service_handle_hint=svc.name or svc.kind,
        size=str(svc_config.get("size", "small")),
        config=svc_config,
        managed_service_id=str(getattr(svc, "guid", "") or svc.pk),
    )
    result = driver.provision(spec)
    return {
        "ok": bool(getattr(result, "ok", False)),
        "handle": str(getattr(result, "handle", "")),
        "message": str(getattr(result, "message", "")),
        "errors": list(getattr(result, "errors", []) or []),
    }


@activity.defn(name="astrolift.managed_service.provision")
async def provision_managed_service(
    managed_service_id: int,
) -> dict[str, Any]:
    """Resolve the ``managed:<kind>:<variant>`` driver and call
    ``provision``. Idempotent — drivers probe for an existing resource
    and return ``ok=True`` if it's already there, so retries are safe.
    Raises on ``ok=False`` so Temporal honors the RetryPolicy.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_provision_sync)(managed_service_id)
    log.info(
        "provision_managed_service result=%s",
        result,
        extra={"managed_service_id": managed_service_id},
    )
    if not result["ok"]:
        raise RuntimeError(
            result["message"] or "driver.provision returned ok=False",
        )
    return result


def _finalize_provision_sync(managed_service_id: int, handle: str) -> None:
    from astrolift_services.models import ManagedService

    svc = ManagedService.all_objects.get(pk=managed_service_id)
    if handle:
        svc.backend_ref = handle
    svc.status = ManagedService.Status.ACTIVE
    svc.status_error = ""
    svc.save(
        update_fields=[
            "backend_ref",
            "status",
            "status_error",
            "updated_at",
            "version",
        ],
    )
    # Materialize the connection-envelope binding rows (#1003). The deploy
    # render builds the per-app ``astrolift-bindings-<slug>`` Secret from
    # these rows (resolving secret refs via the cluster secrets backend) and
    # mounts it via envFrom — without them a provisioned service injects no
    # env and the app can't consume it.
    _sync_binding_rows(svc)


def _sync_binding_rows(svc: Any) -> None:
    """(Re)create ``ManagedServiceBinding`` rows from the driver's connection
    envelope. Idempotent: clears existing rows for the service first so a
    finalize retry doesn't duplicate them."""
    from astrolift_drivers.registry import DriverNotFound, plugins
    from astrolift_services.models import ManagedServiceBinding
    from core.cluster_observability import managed_config_for

    if not svc.backend_ref:
        return
    cluster = svc.app_environment.tenant_cluster
    if cluster is None:
        return
    plugin_slug = cluster.provider_plugin.slug
    variant = getattr(svc, "variant", "") or ""
    try:
        driver_cls = plugins.get(plugin_slug, f"managed:{svc.kind}:{variant}")
    except DriverNotFound:
        try:
            driver_cls = plugins.get(plugin_slug, f"managed:{svc.kind}:")
        except DriverNotFound:
            return
    cfg = managed_config_for(plugin_slug, cluster, kind=svc.kind, variant=variant)
    driver = driver_cls(config=cfg)

    binding_method = getattr(driver, "binding", None)
    if not callable(binding_method):
        return
    from _sdk.managed_service import ServiceHandle

    binding = binding_method(ServiceHandle(handle=svc.backend_ref))
    env_vars = getattr(binding, "env_vars", {}) or {}

    ManagedServiceBinding.objects.filter(managed_service=svc).delete()
    for env_key, value_ref in env_vars.items():
        secret_ref = getattr(value_ref, "secret_ref", None)
        literal = getattr(value_ref, "literal", None)
        ManagedServiceBinding.objects.create(
            managed_service=svc,
            env_key=env_key,
            env_value_ref=secret_ref if secret_ref else (literal or ""),
            is_secret=bool(secret_ref),
        )


@activity.defn(name="astrolift.managed_service.finalize_provision")
async def finalize_managed_service_provision(
    managed_service_id: int,
    handle: str,
) -> None:
    """Persist the driver's backend handle and flip the row to ACTIVE.

    Decoupled from the provision call so a DB hiccup persisting the
    handle can be retried without re-issuing the (idempotent) cloud
    provision.
    """
    from asgiref.sync import sync_to_async

    await sync_to_async(_finalize_provision_sync)(managed_service_id, handle)


def _mark_failed_sync(managed_service_id: int, error: str) -> None:
    from astrolift_services.models import ManagedService

    svc = ManagedService.all_objects.get(pk=managed_service_id)
    svc.status = ManagedService.Status.FAILED
    svc.status_error = error[:4000]
    svc.save(
        update_fields=[
            "status",
            "status_error",
            "updated_at",
            "version",
        ],
    )


@activity.defn(name="astrolift.managed_service.mark_failed")
async def mark_managed_service_failed(
    managed_service_id: int,
    error: str,
) -> None:
    """Flip the row to FAILED with the error surfaced on ``status_error``
    so the operator sees why provisioning didn't complete."""
    from asgiref.sync import sync_to_async

    await sync_to_async(_mark_failed_sync)(managed_service_id, error)
