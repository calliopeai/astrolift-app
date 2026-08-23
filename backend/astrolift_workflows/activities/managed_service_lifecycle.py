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

_DYNAMIC_PVC_VARIANTS = frozenset({"storage_class_pvc", "rook_cephfs"})


class ManagedServicePreflightError(ValueError):
    """A live cluster cannot satisfy a Kubernetes service requirement."""


def _service_cluster(svc):
    """Provisioning cluster for either app-private or project-owned rows."""

    if getattr(svc, "tenant_cluster_id", None):
        return svc.tenant_cluster
    env = getattr(svc, "app_environment", None)
    return getattr(env, "tenant_cluster", None)


def _service_identity(svc: Any) -> str:
    """The id drivers stamp onto the cloud resource and check before mutating it.

    Every spec handed to a driver carries it, not just ``ProvisionSpec``, so an
    update or a teardown can prove the resource behind a deterministic name is
    still the one this row created (#1365).

    ``guid`` and nothing else. It is a non-null UUID with a default on the base
    model, so a saved row always has one. Falling back to ``pk`` would stamp an
    integer where every other path stamps a UUID, and the resource would then
    fail its own ownership check on the next update or teardown forever, which
    fails closed and is therefore permanent.
    """

    guid = str(getattr(svc, "guid", "") or "")
    if not guid:
        raise RuntimeError(
            f"managed service {getattr(svc, 'pk', '?')} has no guid, so a driver "
            f"cannot stamp an identity the next operation could verify"
        )
    return guid


def _resolve_isolation(svc: Any, *, org: Any, cluster: Any) -> str:
    """The isolation mode this row provisions at.

    Nineteen drivers read ``ProvisionSpec.isolation``. Four size the backing
    resource off it (DocumentDB and Neptune instance counts, MemoryDB replicas
    per shard, Memcached nodes) and the rest stamp it as a cloud tag or label.
    The spec field defaults to ``"shared"`` and nothing ever set it, so a
    request for dedicated isolation produced a single-node topology tagged as
    shared, and no compliance floor was enforceable at all.

    No plugin declares per-variant isolation support, so every variant is
    passed as accepting both modes with ``shared`` as its default -- the value
    the spec has always carried. That leaves the resolver's variant gate inert
    rather than guessing a capability the catalogue does not state.
    """

    from astrolift_drivers.isolation import (
        Isolation,
        VariantSupport,
        parse_mode,
        parse_policy,
        resolve,
    )

    decision = resolve(
        manifest_choice=parse_mode(str(getattr(svc, "isolation", "") or "") or None),
        org_policy=parse_policy(getattr(org, "managed_service_isolation_policy", None)),
        org_policy_kind_key=str(svc.kind),
        variant=VariantSupport(
            plugin_slug=getattr(cluster.provider_plugin, "slug", "") or "",
            variant=str(getattr(svc, "variant", "") or ""),
            allowed_modes=frozenset(Isolation),
            default=Isolation.SHARED,
        ),
    )
    return decision.mode.value


def build_provision_spec(svc: Any, *, cluster: Any) -> Any:
    """The ``ProvisionSpec`` this managed-service row provisions through.

    Shared rather than inlined because the identity envelope stamped onto the
    cloud resource is derived entirely from this spec, and the authorized
    adoption operation (#1365) has to write *the same* envelope a provision
    would have written. Two builders would mean an adopted resource carrying a
    subtly different envelope from a created one, which fails its own ownership
    check on the next update and is permanent, because the check fails closed.
    """

    from _sdk.managed_service import ProvisionSpec

    env = svc.app_environment
    app = svc.registered_app
    project = svc.project
    owner = app or project
    org = app.organization if app is not None else project.organization
    environment_id = str(getattr(env, "guid", "") or getattr(cluster, "guid", "") or "")
    environment_name = env.name if env is not None else svc.effective_environment_name
    svc_config = dict(svc.config or {})
    desired_extensions = svc_config.pop("desired_extensions", [])
    if not isinstance(desired_extensions, list) or any(
        not isinstance(value, str) or not value for value in desired_extensions
    ):
        raise ValueError("managed-service desired_extensions must be a string list")
    return ProvisionSpec(
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
        desired_extensions=list(dict.fromkeys(desired_extensions)),
        isolation=_resolve_isolation(svc, org=org, cluster=cluster),
        managed_service_id=_service_identity(svc),
        # The operator's tag channel, distinct from the platform envelope
        # each cloud's own builder stamps. Twenty-one drivers read this and
        # nothing ever set it, so no operator-defined tag — `cost_center`
        # among them — had a path to a resource (#1505). Validated on the
        # way into the org, so it is portable by the time it lands here.
        tags=dict(getattr(org, "default_resource_tags", None) or {}),
    )


def _signals_already_gone(*parts: object) -> bool:
    blob = " ".join(str(p) for p in parts if p).lower()
    return any(marker in blob for marker in _ALREADY_GONE_MARKERS)


def _run_managed_service_preflight(svc: Any, cluster: Any) -> None:
    """Refresh live capabilities and fail before a Kubernetes-backed driver
    mutates the cluster when its required APIs are absent/incompatible."""
    from django.utils import timezone
    from k8s_native.preflight import (
        REQUIREMENTS,
        capabilities_from_payload,
        preflight,
    )

    from core.cluster_management import probe_cluster_capabilities_dispatch

    variant = str(getattr(svc, "variant", "") or "")
    if (str(svc.kind), variant) not in REQUIREMENTS:
        return

    try:
        payload = probe_cluster_capabilities_dispatch(cluster=cluster)
    except Exception as exc:
        raise ManagedServicePreflightError(
            f"cluster {cluster.slug}: live managed-service preflight probe failed: {exc}",
        ) from exc

    cluster.capabilities = payload or {}
    cluster.capabilities_probed_at = timezone.now()
    cluster.save(
        update_fields=[
            "capabilities",
            "capabilities_probed_at",
            "updated_at",
            "version",
        ]
    )
    report = preflight(
        kind=str(svc.kind),
        variant=variant,
        capabilities=capabilities_from_payload(
            cluster_id=str(getattr(cluster, "guid", "") or cluster.slug),
            payload=cluster.capabilities,
        ),
    )
    if report.ok:
        return
    details = "; ".join(f"{failure.code}: {failure.message}" for failure in report.failures)
    hints = "; ".join(dict.fromkeys(report.install_hints))
    suffix = f"; remediation: {hints}" if hints else ""
    raise ManagedServicePreflightError(
        f"cluster {cluster.slug}: managed-service preflight failed: {details}{suffix}",
    )


def _delete_dynamic_pvc_data(svc: Any, cluster: Any, *, force_destroy: bool) -> tuple[bool, str]:
    """Delete materialized dynamic claims only after explicit data confirmation."""

    from astrolift_services.filesystem_bindings import (
        binding_resource_name,
        storage_consumer_key,
    )
    from astrolift_services.models import ManagedServiceAttachment, ManagedServiceVolumeBinding
    from core.app_deploy import namespace_for_app
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    bindings = list(
        ManagedServiceVolumeBinding.objects.filter(
            managed_service=svc,
            source_kind="dynamic_pvc",
            deleted_at__isnull=True,
        ),
    )
    if not bindings:
        return False, "dynamic filesystem has no active volume binding; refusing unverified data deletion"

    attachments = list(
        ManagedServiceAttachment.all_objects.select_related(
            "app_environment__registered_app__organization",
            "agent_environment_spec__organization",
        ).filter(managed_service=svc),
    )
    active = [row for row in attachments if row.deleted_at is None]
    if active and not force_destroy:
        return (
            False,
            "dynamic filesystem still has active attachments; detach them or confirm force_destroy",
        )

    namespaces: set[str] = set()
    for attachment in attachments:
        if attachment.app_environment_id:
            namespaces.add(namespace_for_app(attachment.app_environment.registered_app))
        elif attachment.agent_environment_spec_id:
            from astrolift_workflows.activities.agent_stage import _agent_namespace

            namespaces.add(_agent_namespace(attachment.agent_environment_spec.organization.slug))
    if not svc.project_id and svc.app_environment_id:
        namespaces.add(namespace_for_app(svc.registered_app))
    if not namespaces:
        return True, "no materialized dynamic claims found"

    driver = _driver_for_cluster(cluster)
    cluster_slug = _context_for_cluster(cluster).slug
    try:
        storage_classes = {
            str(row.name): row
            for row in driver.list_storage_classes(cluster_slug)
            if getattr(row, "name", "")
        }
    except Exception as exc:
        return False, f"could not verify StorageClass reclaim policy: {exc}"
    for binding in bindings:
        storage_class = storage_classes.get(str(binding.storage_class_name))
        if storage_class is None:
            return False, f"StorageClass {binding.storage_class_name!r} no longer exists"
        reclaim_policy = str(getattr(storage_class, "reclaim_policy", "") or "")
        if reclaim_policy != "Delete":
            return (
                False,
                f"StorageClass {binding.storage_class_name!r} uses reclaimPolicy "
                f"{reclaim_policy or 'unknown'!r}; refusing to claim backing data was deleted",
            )
    deleted: list[str] = []
    for namespace in sorted(namespaces):
        stubs = []
        for binding in bindings:
            consumer_key = storage_consumer_key(
                binding,
                namespace=namespace,
                consumer_key="deprovision",
            )
            name = binding_resource_name(binding, consumer_key)
            stubs.append(
                {
                    "apiVersion": "v1",
                    "kind": "PersistentVolumeClaim",
                    "metadata": {"name": name, "namespace": namespace},
                },
            )
            deleted.append(f"{namespace}/{name}")
        result = driver.delete_manifests(cluster_slug, namespace, stubs)
        if result is not None and not getattr(result, "ok", True):
            detail = result.summary() if hasattr(result, "summary") else "PVC deletion failed"
            return False, str(detail)
    return True, f"deleted {len(deleted)} dynamic claim(s)"


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
    from astrolift_drivers.managed_resolution import resolve_managed_driver
    from astrolift_drivers.registry import DriverNotFound
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
        resolved = resolve_managed_driver(
            cluster_plugin_slug=plugin_slug,
            kind=svc.kind,
            variant=variant,
        )
    except DriverNotFound as exc:
        raise RuntimeError(f"cluster {cluster.slug}: {exc}") from exc

    cfg = managed_config_for(resolved.plugin_slug, cluster, kind=svc.kind, variant=variant)
    driver = resolved.driver_cls(config=cfg)

    dynamic_cleanup_message = ""
    is_dynamic_pvc = svc.kind == "filesystem" and variant in _DYNAMIC_PVC_VARIANTS
    if delete_data and is_dynamic_pvc:
        cleanup_ok, dynamic_cleanup_message = _delete_dynamic_pvc_data(
            svc,
            cluster,
            force_destroy=force_destroy,
        )
        if not cleanup_ok:
            return {
                "ok": False,
                "message": dynamic_cleanup_message,
                "errors": ["dynamic_pvc_cleanup_refused"],
                "handle": svc.backend_ref or "",
                "retryable": False,
            }

    from _sdk.managed_service import DeprovisionSpec, ServiceHandle

    # ``delete_data=False`` is a preservation claim, not just a driver flag.
    # Complete and record a provider-backed snapshot/export before allowing the
    # destructive half of teardown to start. Unsupported snapshot methods fail
    # here and leave the resource intact instead of reporting retained data
    # that does not exist.
    if not delete_data:
        retained = driver.snapshot(
            ServiceHandle(
                handle=svc.backend_ref,
                managed_service_id=_service_identity(svc),
            )
        )
        snapshot_id = str(getattr(retained, "snapshot_id", "") or "")
        source_handle = str(getattr(retained, "handle", "") or "")
        if not snapshot_id or not source_handle:
            raise RuntimeError(
                "driver did not return a verifiable snapshot/export; refusing data-preserving teardown"
            )
        lifecycle = dict(getattr(svc, "lifecycle_policy", None) or {})
        lifecycle["last_retained_snapshot"] = {
            "snapshot_id": snapshot_id,
            "source_handle": source_handle,
            "created_at": str(getattr(retained, "created_at", "") or ""),
        }
        svc.lifecycle_policy = lifecycle
        svc.save(update_fields=["lifecycle_policy", "updated_at", "version"])

    deprovision_config = dict(svc.config or {})
    if delete_data and is_dynamic_pvc:
        deprovision_config["_dynamic_claim_cleanup_confirmed"] = True
    spec = DeprovisionSpec(
        handle=svc.backend_ref or "",
        config=deprovision_config,
        managed_service_id=_service_identity(svc),
    )
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
    if dynamic_cleanup_message:
        message = f"{message}; {dynamic_cleanup_message}" if message else dynamic_cleanup_message
    errors = list(getattr(result, "errors", []) or [])
    retryable = bool(getattr(result, "retryable", True))
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
        "retryable": retryable,
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
        from temporalio.exceptions import ApplicationError

        raise ApplicationError(
            result["message"] or "driver.deprovision returned ok=False",
            non_retryable=not bool(result.get("retryable", False)),
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
    from astrolift_drivers.managed_resolution import resolve_managed_driver
    from astrolift_drivers.registry import DriverNotFound
    from astrolift_services.models import ManagedService
    from core.cluster_observability import managed_config_for

    svc = ManagedService.all_objects.select_related(
        "registered_app__organization",
        "project__organization",
        "app_environment__tenant_cluster__provider_plugin",
        "tenant_cluster__provider_plugin",
    ).get(pk=managed_service_id)
    cluster = _service_cluster(svc)
    if cluster is None:
        raise RuntimeError(
            f"managed service {svc.pk} env has no tenant_cluster bound",
        )
    plugin_slug = cluster.provider_plugin.slug
    variant = getattr(svc, "variant", "") or ""
    _run_managed_service_preflight(svc, cluster)
    try:
        resolved = resolve_managed_driver(
            cluster_plugin_slug=plugin_slug,
            kind=svc.kind,
            variant=variant,
        )
    except DriverNotFound as exc:
        raise RuntimeError(f"cluster {cluster.slug}: {exc}") from exc

    cfg = managed_config_for(resolved.plugin_slug, cluster, kind=svc.kind, variant=variant)
    driver = resolved.driver_cls(config=cfg)

    spec = build_provision_spec(svc, cluster=cluster)
    restore = dict((getattr(svc, "lifecycle_policy", None) or {}).get("restore") or {})
    if restore and not svc.backend_ref:
        from _sdk.managed_service import SnapshotHandle

        result = driver.restore(
            SnapshotHandle(
                handle=str(restore["source_handle"]),
                snapshot_id=str(restore["snapshot_id"]),
                created_at=str(restore.get("created_at", "")),
            ),
            spec,
        )
    else:
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


def _update_sync(managed_service_id: int) -> dict[str, Any]:
    from astrolift_drivers.managed_resolution import resolve_managed_driver
    from astrolift_drivers.registry import DriverNotFound
    from astrolift_services.models import ManagedService
    from core.cluster_observability import managed_config_for

    svc = ManagedService.all_objects.select_related(
        "app_environment__tenant_cluster__provider_plugin",
        "tenant_cluster__provider_plugin",
    ).get(pk=managed_service_id)
    cluster = _service_cluster(svc)
    if cluster is None:
        raise RuntimeError(f"managed service {svc.pk} has no tenant cluster")
    if not svc.backend_ref:
        raise ValueError("managed service has no backend handle; reprovision it instead")

    plugin_slug = cluster.provider_plugin.slug
    variant = getattr(svc, "variant", "") or ""
    _run_managed_service_preflight(svc, cluster)
    try:
        resolved = resolve_managed_driver(
            cluster_plugin_slug=plugin_slug,
            kind=svc.kind,
            variant=variant,
        )
    except DriverNotFound as exc:
        raise ValueError(f"cluster {cluster.slug}: {exc}") from exc

    cfg = managed_config_for(resolved.plugin_slug, cluster, kind=svc.kind, variant=variant)
    driver = resolved.driver_cls(config=cfg)

    from _sdk.managed_service import UpdateSpec

    desired = dict(svc.config or {})
    result = driver.update(
        UpdateSpec(
            handle=svc.backend_ref,
            size=str(desired["size"]) if "size" in desired else None,
            config=desired,
            managed_service_id=_service_identity(svc),
        ),
    )
    return {
        "ok": bool(getattr(result, "ok", False)),
        "handle": str(getattr(result, "handle", "") or svc.backend_ref),
        "message": str(getattr(result, "message", "")),
        "errors": list(getattr(result, "errors", []) or []),
        "retryable": bool(getattr(result, "retryable", False)),
    }


@activity.defn(name="astrolift.managed_service.update")
async def update_managed_service(managed_service_id: int) -> dict[str, Any]:
    """Apply the row's desired config through ``ManagedServiceDriver.update``."""
    from asgiref.sync import sync_to_async
    from temporalio.exceptions import ApplicationError

    activity.heartbeat()
    try:
        result = await sync_to_async(_update_sync)(managed_service_id)
    except (TypeError, ValueError) as exc:
        raise ApplicationError(str(exc), non_retryable=True) from exc
    log.info(
        "update_managed_service result=%s",
        result,
        extra={"managed_service_id": managed_service_id},
    )
    if not result["ok"]:
        detail = result["message"] or "; ".join(result["errors"])
        raise ApplicationError(
            detail or "driver.update returned ok=False",
            non_retryable=not result["retryable"],
        )
    return result


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
    from temporalio.exceptions import ApplicationError

    try:
        result = await sync_to_async(_provision_sync)(managed_service_id)
    except ManagedServicePreflightError as exc:
        raise ApplicationError(str(exc), non_retryable=True) from exc
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
    from astrolift_drivers.managed_resolution import resolve_managed_driver
    from astrolift_drivers.registry import DriverNotFound
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
        resolved = resolve_managed_driver(
            cluster_plugin_slug=plugin_slug,
            kind=svc.kind,
            variant=variant,
        )
    except DriverNotFound:
        return "available"
    cfg = managed_config_for(resolved.plugin_slug, cluster, kind=svc.kind, variant=variant)
    driver = resolved.driver_cls(config=cfg)
    status_method = getattr(driver, "status", None)
    if not callable(status_method):
        return "available"
    from _sdk.managed_service import ServiceHandle

    probe = ServiceHandle(handle=handle, managed_service_id=_service_identity(svc))
    return str(getattr(status_method(probe), "state", "available"))


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


def _connection_secret_path(svc: Any) -> str:
    """Where this binding's connection material lives in the secrets backend.

    ``ManagedService.connection_secret_ref`` had no writer, so
    ``revealManagedServiceConnection`` answered ``placeholder:pending`` for
    every service in every org, and the audit row it emits alongside recorded
    an empty ref, leaving the trail unable to say which secret was disclosed.

    Composed from stable identifiers rather than taken from the driver so a
    reprovision of the same binding resolves to the same path.
    """

    from astrolift_drivers.connection_secret import secret_storage_path

    app = svc.registered_app
    owner = app or svc.project
    org = app.organization if app is not None else svc.project.organization
    return secret_storage_path(
        env_slug=svc.effective_environment_name,
        org_slug=org.slug,
        app_slug=owner.slug,
        service_name=svc.name or svc.kind,
    )


def _finalize_provision_sync(managed_service_id: int, handle: str) -> list[int]:
    from astrolift_services.models import ManagedService

    svc = ManagedService.all_objects.get(pk=managed_service_id)
    if handle:
        svc.backend_ref = handle
    svc.applied_config = dict(svc.config or {})
    svc.connection_secret_ref = _connection_secret_path(svc)
    svc.status = ManagedService.Status.ACTIVE
    svc.status_error = ""
    svc.save(
        update_fields=[
            "backend_ref",
            "applied_config",
            "connection_secret_ref",
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
    return _sync_binding_rows(svc)


def _finalize_update_sync(managed_service_id: int, handle: str) -> list[int]:
    from django.utils import timezone

    from astrolift_services.models import ManagedService

    svc = ManagedService.all_objects.get(pk=managed_service_id)
    if handle:
        svc.backend_ref = handle
    svc.applied_config = dict(svc.config or {})
    svc.status = ManagedService.Status.ACTIVE
    svc.status_error = ""
    svc.operation_completed_at = timezone.now()
    svc.save(
        update_fields=[
            "backend_ref",
            "applied_config",
            "status",
            "status_error",
            "operation_completed_at",
            "updated_at",
            "version",
        ],
    )
    return _sync_binding_rows(svc)


@activity.defn(name="astrolift.managed_service.finalize_update")
async def finalize_managed_service_update(managed_service_id: int, handle: str) -> list[int]:
    """Persist the applied config, flip to ACTIVE, and report which binding
    rows carry a value the workload has not seen yet (spec 06 §4.8 step 6)."""
    from asgiref.sync import sync_to_async

    return await sync_to_async(_finalize_update_sync)(managed_service_id, handle)


def _managed_binding_for(svc: Any) -> Any:
    """Resolve ``svc``'s managed-service driver and return its connection
    ``Binding`` (``env_vars`` + ``iam_grants``), or ``None`` when the service
    has no backend handle / cluster / registered driver.

    Shared by ``_sync_binding_rows`` (reads ``env_vars``) and the workload-
    identity activity (reads ``iam_grants``) so both resolve the driver the
    same way.
    """
    from astrolift_drivers.managed_resolution import resolve_managed_driver
    from astrolift_drivers.registry import DriverNotFound
    from core.cluster_observability import managed_config_for

    if not svc.backend_ref:
        return None
    cluster = _service_cluster(svc)
    if cluster is None:
        return None
    plugin_slug = cluster.provider_plugin.slug
    variant = getattr(svc, "variant", "") or ""
    try:
        resolved = resolve_managed_driver(
            cluster_plugin_slug=plugin_slug,
            kind=svc.kind,
            variant=variant,
        )
    except DriverNotFound:
        return None
    cfg = managed_config_for(resolved.plugin_slug, cluster, kind=svc.kind, variant=variant)
    driver = resolved.driver_cls(config=cfg)

    binding_method = getattr(driver, "binding", None)
    if not callable(binding_method):
        return None
    from _sdk.managed_service import ServiceHandle

    handle = ServiceHandle(handle=svc.backend_ref, managed_service_id=_service_identity(svc))
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


def _sync_binding_rows(svc: Any) -> list[int]:
    """Atomically replace the driver's environment and volume envelopes.

    Returns the ids of the env binding rows whose value differs from what the
    previous envelope carried (a brand-new key counts as a difference). Those
    are the rows a running pod has stale copies of, so they are what the
    dependent-workload bounce keys off — an update that rewrites the same
    endpoint and password must not restart anybody's pods.
    """
    from _sdk.managed_service import VolumeMount
    from django.db import transaction

    from astrolift_services.models import (
        ManagedServiceBinding,
        ManagedServiceVolumeBinding,
    )

    binding = _managed_binding_for(svc)
    if binding is None:
        return []
    env_vars = getattr(binding, "env_vars", {}) or {}
    volume_mounts = list(getattr(binding, "pod_volume_mounts", ()) or ())

    if volume_mounts and str(getattr(svc, "kind", "")) not in {"filesystem", "nfs"}:
        raise ValueError("only filesystem managed services may emit pod volume mounts")

    names: set[str] = set()
    for volume in volume_mounts:
        if not isinstance(volume, VolumeMount):
            raise TypeError("managed-service pod_volume_mounts must contain VolumeMount values")
        if volume.name in names:
            raise ValueError(f"managed-service binding emitted duplicate volume name {volume.name!r}")
        names.add(volume.name)

    rebound: list[int] = []
    with transaction.atomic():
        prior = dict(
            ManagedServiceBinding.objects.filter(managed_service=svc).values_list(
                "env_key",
                "env_value_ref",
            ),
        )
        ManagedServiceBinding.objects.filter(managed_service=svc).delete()
        ManagedServiceVolumeBinding.objects.filter(managed_service=svc).delete()
        for env_key, value_ref in env_vars.items():
            secret_ref = getattr(value_ref, "secret_ref", None)
            literal = getattr(value_ref, "literal", None)
            env_value_ref = secret_ref if secret_ref else (literal or "")
            row = ManagedServiceBinding.objects.create(
                managed_service=svc,
                env_key=env_key,
                env_value_ref=env_value_ref,
                is_secret=bool(secret_ref),
            )
            if prior.get(env_key) != env_value_ref:
                rebound.append(row.pk)
        for volume in volume_mounts:
            ManagedServiceVolumeBinding.objects.create(
                managed_service=svc,
                name=volume.name,
                mount_path=volume.mount_path,
                sub_path=volume.sub_path or "",
                source_kind=str(volume.source_kind),
                protocol=volume.protocol,
                claim_name=volume.claim_name,
                claim_namespace=volume.claim_namespace,
                storage_class_name=volume.storage_class_name,
                csi_driver=volume.csi_driver,
                volume_handle=volume.volume_handle,
                volume_attributes=dict(volume.volume_attributes),
                secret_refs=dict(volume.secret_refs),
                secret_literals=dict(volume.secret_literals),
                mount_options=list(volume.mount_options),
                read_only=volume.read_only,
                capacity=volume.capacity,
                access_modes=list(volume.access_modes),
                workload_names=list(volume.workload_names),
                container_names=list(volume.container_names),
            )
    return rebound


@activity.defn(name="astrolift.managed_service.finalize_provision")
async def finalize_managed_service_provision(
    managed_service_id: int,
    handle: str,
) -> list[int]:
    """Persist the driver's backend handle and flip the row to ACTIVE.

    Decoupled from the provision call so a DB hiccup persisting the
    handle can be retried without re-issuing the (idempotent) cloud
    provision.

    Returns the binding row ids whose value the dependent workloads have not
    seen yet, which the bounce activity turns into the set of app
    environments to restart (spec 06 §4.7 step 5).
    """
    from asgiref.sync import sync_to_async

    return await sync_to_async(_finalize_provision_sync)(managed_service_id, handle)


def _mark_failed_sync(managed_service_id: int, error: str) -> None:
    from django.utils import timezone

    from astrolift_services.models import ManagedService

    svc = ManagedService.all_objects.get(pk=managed_service_id)
    svc.status = ManagedService.Status.FAILED
    svc.status_error = error[:4000]
    update_fields = ["status", "status_error", "updated_at", "version"]
    if svc.operation_kind:
        svc.operation_completed_at = timezone.now()
        update_fields.append("operation_completed_at")
    svc.save(update_fields=update_fields)


@activity.defn(name="astrolift.managed_service.mark_failed")
async def mark_managed_service_failed(
    managed_service_id: int,
    error: str,
) -> None:
    """Flip the row to FAILED with the error surfaced on ``status_error``
    so the operator sees why provisioning didn't complete."""
    from asgiref.sync import sync_to_async

    await sync_to_async(_mark_failed_sync)(managed_service_id, error)


def _dependent_app_environment_ids(svc: Any, rebound_binding_ids: list[int]) -> tuple[int, ...]:
    """App environments whose running pods hold a stale copy of a rebound
    binding (spec 06 §4.7 step 5).

    A service reaches an environment two ways: app-private rows carry the env
    directly, project-owned rows reach it through an attachment. The pairing
    goes to ``managed_service_states.workloads_to_redeploy`` rather than being
    computed here so the "which workloads does a rebind touch" rule stays in
    the phase contract these workflows are specified against.
    """
    from astrolift_services.models import ManagedServiceAttachment, ManagedServiceBinding
    from astrolift_workflows.managed_service_states import (
        WorkloadBinding,
        workloads_to_redeploy,
    )

    env_ids: set[int] = set()
    if svc.app_environment_id:
        env_ids.add(int(svc.app_environment_id))
    env_ids.update(
        int(pk)
        for pk in ManagedServiceAttachment.objects.filter(
            managed_service=svc,
            app_environment__isnull=False,
            deleted_at__isnull=True,
        ).values_list("app_environment_id", flat=True)
    )
    binding_ids = list(
        ManagedServiceBinding.objects.filter(
            managed_service=svc,
            deleted_at__isnull=True,
        ).values_list("pk", flat=True),
    )
    all_bindings = [
        WorkloadBinding(workload_id=env_id, binding_id=int(binding_id))
        for env_id in sorted(env_ids)
        for binding_id in binding_ids
    ]
    return workloads_to_redeploy(
        rebound_binding_ids=[int(pk) for pk in rebound_binding_ids],
        all_bindings=all_bindings,
    )


def _bounce_dependent_workloads_sync(
    managed_service_id: int,
    rebound_binding_ids: list[int],
) -> int:
    """Roll every workload that consumes a rebound binding, returning the count.

    The connection envelope lands in the per-app ``astrolift-bindings-<slug>``
    Secret, and a Secret change restarts nothing on its own: pods keep the
    values they read at start, so a rotated password or a moved endpoint leaves
    the app failing at connect time with nothing tying the failure to the
    service operation. Same restart-annotation patch the bundle-rotation
    bounce (#365) uses.

    Best-effort per workload — a driver that cannot list or patch is logged and
    skipped, because the new values still land on the environment's next deploy
    and a failed bounce must not fail the service operation.
    """
    from datetime import UTC, datetime

    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_services.models import ManagedService
    from core.app_deploy import namespace_for_app
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    if not rebound_binding_ids:
        return 0
    svc = ManagedService.all_objects.get(pk=managed_service_id)
    env_ids = _dependent_app_environment_ids(svc, rebound_binding_ids)
    if not env_ids:
        return 0

    now_iso = datetime.now(UTC).isoformat()
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": now_iso,
                        "astrolift.io/managed-service-rebound-at": now_iso,
                        "astrolift.io/managed-service": str(svc.guid),
                    },
                },
            },
        },
    }

    bounced = 0
    environments = (
        AppEnvironment.all_objects.filter(pk__in=env_ids, tenant_cluster__isnull=False)
        .select_related("registered_app__organization", "tenant_cluster__provider_plugin")
        .order_by("pk")
    )
    for env in environments:
        namespace = namespace_for_app(env.registered_app)
        driver = _driver_for_cluster(env.tenant_cluster)
        cluster_slug = _context_for_cluster(env.tenant_cluster).slug
        patch_workload = getattr(driver, "patch_workload", None)
        if not callable(patch_workload):
            log.warning(
                "cluster driver for %s has no patch_workload — workloads keep "
                "the stale managed-service envelope until their next deploy",
                env.tenant_cluster.slug,
            )
            continue
        targets: list[tuple[str, str]] = []
        list_workloads = getattr(driver, "list_workloads", None)
        if callable(list_workloads):
            try:
                targets = [
                    (kind, name)
                    for kind, name in list_workloads(cluster_slug, namespace)
                    if kind == "Deployment"
                ]
            except Exception as exc:  # noqa: BLE001 — best-effort discovery
                log.warning(
                    "list_workloads on cluster=%s ns=%s failed: %s",
                    cluster_slug,
                    namespace,
                    exc,
                )
                targets = []
        if not targets:
            # Same fallback the rotation bounce uses: the renderer names a
            # single-workload app's Deployment after the app slug.
            targets = [("Deployment", str(env.registered_app.slug))]
        for kind, name in targets:
            try:
                patch_workload(cluster_slug, namespace, kind, name, patch)
                bounced += 1
            except Exception as exc:  # noqa: BLE001 — a stale pod set is not a failed provision
                log.warning(
                    "patch_workload(%s/%s) on cluster=%s failed: %s",
                    kind,
                    name,
                    cluster_slug,
                    exc,
                )
    return bounced


@activity.defn(name="astrolift.managed_service.bounce_dependent_workloads")
async def bounce_workloads_bound_to_managed_service(
    managed_service_id: int,
    rebound_binding_ids: list[int],
) -> int:
    """Restart the workloads consuming the bindings finalize just rewrote."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_bounce_dependent_workloads_sync)(
        managed_service_id,
        rebound_binding_ids,
    )
