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

# Markers a driver error / failed-result carries when the backend resource is
# already gone. Idempotent teardown (#1034): a re-run over a partially-torn-
# down app must converge — "already deleted" is success, not a fatal error
# that re-raises and strands the row (and the app) at deprovisioning forever.
_ALREADY_GONE_MARKERS = (
    "nosuchbucket",
    "nosuchentity",
    "nosuchhostedzone",
    "resourcenotfound",
    "notfound",
    "not found",
    "does not exist",
    "already deleted",
)


def _service_cluster(svc):
    """Provisioning cluster for either app-private or project-owned rows."""

    if getattr(svc, "tenant_cluster_id", None):
        return svc.tenant_cluster
    env = getattr(svc, "app_environment", None)
    return getattr(env, "tenant_cluster", None)


def _signals_already_gone(*parts: object) -> bool:
    blob = " ".join(str(p) for p in parts if p).lower()
    return any(marker in blob for marker in _ALREADY_GONE_MARKERS)


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
        "tenant_cluster__provider_plugin",
    ).get(pk=managed_service_id)
    cluster = _service_cluster(svc)
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

    spec = DeprovisionSpec(handle=svc.backend_ref or "", config=dict(svc.config or {}))
    try:
        result = driver.deprovision(
            spec,
            delete_data=delete_data,
            force_destroy=force_destroy,
        )
    except Exception as exc:
        # A driver that RAISES because the backend resource is already gone
        # (NotFoundError / NoSuchBucket / 404) is an idempotent no-op on
        # teardown — return soft-success so the row finalizes (#1034). Any
        # other exception propagates so Temporal honors the RetryPolicy.
        if _signals_already_gone(type(exc).__name__, exc):
            return {
                "ok": True,
                "message": f"already deprovisioned: {exc}",
                "errors": [],
                "handle": svc.backend_ref or "",
            }
        raise
    ok = bool(getattr(result, "ok", False))
    message = str(getattr(result, "message", ""))
    errors = list(getattr(result, "errors", []) or [])
    if not ok and _signals_already_gone(message, *errors):
        # Driver REPORTED a not-found failure (e.g. S3 "empty failed:
        # NoSuchBucket") — the resource is already gone, so deprovision is
        # effectively complete. Coerce to success so the workflow finalizes
        # the row instead of re-raising into a stuck teardown (#1034).
        return {
            "ok": True,
            "message": f"already deprovisioned: {message}",
            "errors": [],
            "handle": str(getattr(result, "handle", "")),
        }
    return {
        "ok": ok,
        "message": message,
        "errors": errors,
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
    if svc.deleted_at is not None:
        # Already finalized — a resumed/idempotent teardown re-running the
        # fan-out over an already-soft-deleted row is a clean no-op (#1034).
        return
    # Drive the row off any transient status (pending / active /
    # provisioning / deprovisioning) before soft-deleting it. Without this
    # a teardown that deleted the backend resource could leave the platform
    # row stuck at pending/active, outliving its cloud resource and
    # stranding the app at tearing_down forever (#1034). There is no
    # terminal DEPROVISIONED status; the soft-delete IS the terminal state,
    # and DEPROVISIONING is the truthful last-known intent on the dead row.
    if svc.status != ManagedService.Status.DEPROVISIONING:
        svc.status = ManagedService.Status.DEPROVISIONING
        svc.save(update_fields=["status", "updated_at", "version"])
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
        "project__organization",
        "app_environment__tenant_cluster__provider_plugin",
        "tenant_cluster__provider_plugin",
    ).get(pk=managed_service_id)
    env = svc.app_environment
    cluster = _service_cluster(svc)
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
    project = svc.project
    owner = app or project
    org = app.organization if app is not None else project.organization
    environment_id = str(getattr(env, "guid", "") or getattr(cluster, "guid", "") or "")
    environment_name = env.name if env is not None else svc.effective_environment_name
    svc_config = dict(svc.config or {})
    spec = ProvisionSpec(
        organization_id=str(getattr(org, "guid", "") or ""),
        organization_slug=getattr(org, "slug", "") or "",
        app_id=str(getattr(owner, "guid", "") or ""),
        app_slug=owner.slug,
        environment_id=environment_id,
        environment_name=environment_name,
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
        # Capability-only services (Bedrock on-demand) report ready=True so
        # the workflow skips its status()-poll readiness wait (#1038).
        "ready": bool(getattr(result, "ready", False)),
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


def _check_ready_sync(managed_service_id: int, handle: str) -> str:
    """Return the driver-reported readiness state for the backend resource
    (provisioning | available | updating | error | deprovisioned ...)."""
    from astrolift_drivers.registry import DriverNotFound, plugins
    from astrolift_services.models import ManagedService
    from core.cluster_observability import managed_config_for

    svc = ManagedService.all_objects.select_related(
        "app_environment__tenant_cluster__provider_plugin",
        "tenant_cluster__provider_plugin",
    ).get(pk=managed_service_id)
    cluster = _service_cluster(svc)
    if cluster is None:
        return "available"  # nothing to wait on
    plugin_slug = cluster.provider_plugin.slug
    variant = getattr(svc, "variant", "") or ""
    try:
        driver_cls = plugins.get(plugin_slug, f"managed:{svc.kind}:{variant}")
    except DriverNotFound:
        try:
            driver_cls = plugins.get(plugin_slug, f"managed:{svc.kind}:")
        except DriverNotFound:
            return "available"
    cfg = managed_config_for(plugin_slug, cluster, kind=svc.kind, variant=variant)
    driver = driver_cls(config=cfg)
    status_method = getattr(driver, "status", None)
    if not callable(status_method):
        return "available"
    from _sdk.managed_service import ServiceHandle

    return str(getattr(status_method(ServiceHandle(handle=handle)), "state", "available"))


@activity.defn(name="astrolift.managed_service.check_ready")
async def check_managed_service_ready(
    managed_service_id: int,
    handle: str,
) -> str:
    """One readiness probe of the backing resource. The workflow polls this
    (with a timer between calls) until ``available`` so finalize materializes
    bindings against a real endpoint — RDS/ElastiCache report ``provisioning``
    until the instance is up; S3 is ``available`` immediately (#1009)."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_check_ready_sync)(managed_service_id, handle)


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


def _managed_binding_for(svc: Any) -> Any:
    """Resolve ``svc``'s managed-service driver and return its connection
    ``Binding`` (``env_vars`` + ``iam_grants``), or ``None`` when the service
    has no backend handle / cluster / registered driver.

    Shared by ``_sync_binding_rows`` (reads ``env_vars``) and the workload-
    identity activity (reads ``iam_grants``) so both resolve the driver the
    same way.
    """
    from astrolift_drivers.registry import DriverNotFound, plugins
    from core.cluster_observability import managed_config_for

    if not svc.backend_ref:
        return None
    cluster = _service_cluster(svc)
    if cluster is None:
        return None
    plugin_slug = cluster.provider_plugin.slug
    variant = getattr(svc, "variant", "") or ""
    try:
        driver_cls = plugins.get(plugin_slug, f"managed:{svc.kind}:{variant}")
    except DriverNotFound:
        try:
            driver_cls = plugins.get(plugin_slug, f"managed:{svc.kind}:")
        except DriverNotFound:
            return None
    cfg = managed_config_for(plugin_slug, cluster, kind=svc.kind, variant=variant)
    driver = driver_cls(config=cfg)

    binding_method = getattr(driver, "binding", None)
    if not callable(binding_method):
        return None
    from _sdk.managed_service import ServiceHandle

    handle = ServiceHandle(handle=svc.backend_ref)
    # Thread the operator-supplied ``ManagedService.config`` into the
    # binding so config-driven binding fields render (#1038): the SES
    # driver folds ``from_name``/``reply_to``/``return_path``/
    # ``env_senders`` into its env_vars from this dict. Pass it only to
    # drivers whose ``binding`` signature accepts ``config`` — the
    # majority take ``(self, handle)`` only, and an unconditional kwarg
    # would TypeError those (the 7 working kinds). Defaults empty, so
    # config-agnostic callers are unaffected.
    import inspect

    svc_config = dict(getattr(svc, "config", None) or {})
    try:
        accepts_config = "config" in inspect.signature(binding_method).parameters
    except (TypeError, ValueError):
        accepts_config = False
    if accepts_config:
        return binding_method(handle, config=svc_config)
    return binding_method(handle)


def _sync_binding_rows(svc: Any) -> None:
    """(Re)create ``ManagedServiceBinding`` rows from the driver's connection
    envelope. Idempotent: clears existing rows for the service first so a
    finalize retry doesn't duplicate them."""
    from astrolift_services.models import ManagedServiceBinding

    binding = _managed_binding_for(svc)
    if binding is None:
        return
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
