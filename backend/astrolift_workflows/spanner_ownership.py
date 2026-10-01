"""Internal recorded-container proof; cloud inventory is a separate driver gate."""

from __future__ import annotations

import functools
import hashlib
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from django.db import connection, transaction
from django.utils import timezone

_MAX_CONTENDERS = 1000


def is_spanner(resolved: Any) -> bool:
    from gcp.managed.graph_spanner import SpannerGraphDriver

    return isinstance(resolved.driver_cls, type) and issubclass(resolved.driver_cls, SpannerGraphDriver)


def target_instance(svc: Any, cfg: Any) -> str:
    from astrolift_services.secret_ref_config import service_organization
    from gcp.managed.graph_spanner import spanner_instance_id

    return spanner_instance_id(
        managed_service_id=str(svc.guid),
        organization_id=str(service_organization(svc).guid),
        recorded_handle=str(svc.backend_ref or ""),
        instance_id=str((svc.config or {}).get("instance_id") or ""),
        shared_instance_id=cfg.shared_instance_id,
        instance_name_prefix=cfg.instance_name_prefix,
    )


def placement_config(cluster: Any) -> SimpleNamespace:
    """Only persisted non-secret target fields, without credentials or stores.

    This is the exact Spanner target mapping used by the GCP managed config
    builder. Full driver configuration is constructed only after row locks.
    """
    pc, ac = cluster.provider_config or {}, cluster.auth_config or {}
    return SimpleNamespace(
        project_id=str(
            pc.get("project_id")
            or pc.get("gcp_project_id")
            or ac.get("project_id")
            or ac.get("gcp_project_id")
            or ""
        ),
        shared_instance_id=str(pc.get("spanner_shared_instance_id", "")),
        instance_name_prefix=str(pc.get("spanner_instance_name_prefix", "astrolift")),
    )


def resource_identity(svc: Any, resolved: Any, cfg: Any) -> dict[str, str]:
    from astrolift_services.secret_ref_config import service_organization
    from gcp.managed.graph_spanner import _parse_handle

    from astrolift_workflows.activities.managed_service_lifecycle import _service_cluster

    cluster = _service_cluster(svc)
    organization = service_organization(svc)
    instance, database = _parse_handle(svc.backend_ref)
    if cluster is None or organization is None or not cfg.project_id:
        raise ValueError("Spanner resource identity is not established")
    return {
        "service_id": str(svc.guid),
        "organization_id": str(organization.guid),
        "cluster_id": str(cluster.guid),
        "provider_id": str(cluster.provider_plugin.guid),
        "driver": f"{resolved.driver_cls.__module__}.{resolved.driver_cls.__qualname__}",
        "project_id": cfg.project_id,
        "instance_id": instance,
        "database_id": database,
        "handle": svc.backend_ref,
    }


def validate_observed_placement(svc: Any, resolved: Any) -> None:
    """Refuse retargeting before config construction can resolve credentials.

    Legacy rows have no historical project provenance. A successful provider
    observation pins only the placement actually checked at that observation.
    """
    from astrolift_services.secret_ref_config import service_organization

    from astrolift_workflows.activities.managed_service_lifecycle import _service_cluster

    identity = svc.provider_placement_identity
    if identity is None:
        return
    cluster = _service_cluster(svc)
    organization = service_organization(svc)
    if cluster is None or organization is None or not isinstance(identity, dict):
        raise ValueError("Spanner observed placement is invalid; refusing provider work")
    current = {
        "service_id": str(svc.guid),
        "organization_id": str(organization.guid),
        "cluster_id": str(cluster.guid),
        "provider_id": str(cluster.provider_plugin.guid),
        "driver": f"{resolved.driver_cls.__module__}.{resolved.driver_cls.__qualname__}",
        "project_id": placement_config(cluster).project_id,
        "handle": svc.backend_ref,
    }
    try:
        observed = datetime.fromisoformat(identity["observed_at"])
        valid = (
            identity.get("version") == 1
            and timezone.is_aware(observed)
            and observed <= timezone.now()
            and is_spanner(resolved)
            and all(identity.get(key) == value for key, value in current.items())
        )
        from gcp.managed.graph_spanner import _parse_handle

        instance, database = _parse_handle(svc.backend_ref)
        valid = valid and identity.get("instance_id") == instance and identity.get("database_id") == database
    except (KeyError, ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError("Spanner observed placement changed; refusing provider work")


def record_placement(svc: Any, *, resolved: Any, cfg: Any, handle: str) -> None:
    if not is_spanner(resolved):
        return
    # This function runs only after an actual successful driver result, inside
    # the container/source lock transaction. Handle and observation are atomic.
    if not handle or (svc.backend_ref and svc.backend_ref != handle):
        raise ValueError("Spanner successful result changed its recorded handle")
    svc.backend_ref = handle
    svc.provider_placement_identity = {
        "version": 1,
        **resource_identity(svc, resolved, cfg),
        "observed_at": timezone.now().isoformat(),
    }
    svc.save(update_fields=["backend_ref", "provider_placement_identity", "updated_at", "version"])


def cleanup_confirmed(svc: Any, resolved: Any, cfg: Any) -> bool:
    receipt = svc.provider_cleanup_receipt
    if svc.deleted_at is None or not isinstance(receipt, dict) or receipt.get("version") != 1:
        return False
    try:
        observed = datetime.fromisoformat(receipt["observed_at"])
        return (
            timezone.is_aware(observed)
            and observed <= timezone.now()
            and all(receipt.get(key) == value for key, value in resource_identity(svc, resolved, cfg).items())
        )
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def record_cleanup(svc: Any, *, resolved: Any, cfg: Any) -> None:
    if not is_spanner(resolved):
        return
    receipt = {
        "version": 1,
        **resource_identity(svc, resolved, cfg),
        "observed_at": timezone.now().isoformat(),
    }
    svc.provider_cleanup_receipt = receipt
    svc.save(update_fields=["provider_cleanup_receipt", "updated_at", "version"])


def clear_cleanup(svc: Any, *, resolved: Any) -> None:
    if is_spanner(resolved) and svc.provider_cleanup_receipt is not None:
        svc.provider_cleanup_receipt = None
        svc.save(update_fields=["provider_cleanup_receipt", "updated_at", "version"])


def container_exclusive(svc: Any, *, resolved: Any, cfg: Any) -> bool:
    from astrolift_drivers.managed_resolution import resolve_managed_driver
    from astrolift_services.models import ManagedService

    from astrolift_workflows.activities.managed_service_lifecycle import _service_cluster

    if not is_spanner(resolved) or not cfg.project_id:
        return False
    try:
        target = target_instance(svc, cfg)
    except (ValueError, AttributeError):
        return False
    others = list(
        ManagedService.all_objects.filter(kind=ManagedService.Kind.GRAPH_DB)
        .exclude(pk=svc.pk)
        .select_related(
            "registered_app__organization",
            "project__organization",
            "app_environment__tenant_cluster__provider_plugin",
            "tenant_cluster__provider_plugin",
        )
        .order_by("pk")[: _MAX_CONTENDERS + 1]
    )
    if len(others) > _MAX_CONTENDERS:
        return False
    for other in others:
        cluster = _service_cluster(other)
        if cluster is None:
            return False
        try:
            other_resolved = resolve_managed_driver(
                cluster_plugin_slug=cluster.provider_plugin.slug, kind=other.kind, variant=other.variant
            )
            if other_resolved.driver_cls is not resolved.driver_cls:
                continue
            validate_observed_placement(other, other_resolved)
            other_cfg = placement_config(cluster)
            if not other_cfg.project_id:
                return False
            if other_cfg.project_id != cfg.project_id:
                continue
            if cleanup_confirmed(other, other_resolved, other_cfg):
                continue
            if target_instance(other, other_cfg) == target:
                return False
            # A desired explicit target is also a contender while its previous
            # handle is retained. Never assume a failed move cleaned either.
            if (other.config or {}).get("instance_id") == target:
                return False
        except Exception:  # noqa: BLE001 - unknown placement cannot establish exclusivity
            return False
    return True


def guard_spanner_container(function):
    """Serialize physical-container operations and recheck placement under locks.

    Tenant row creation may enqueue work during a call; its provider operation
    must acquire this same driver/project/instance lock before taking a proof.
    Neither a caller-supplied config flag nor a previous cached proof is used.
    """

    @functools.wraps(function)
    def guarded(managed_service_id: int, *args, **kwargs):
        from astrolift_clusters.models import ProviderPlugin, TenantCluster
        from astrolift_drivers.managed_resolution import resolve_managed_driver
        from astrolift_identity.models import Organization, Project
        from astrolift_lifecycle.models import AppEnvironment
        from astrolift_registry.models import RegisteredApp
        from astrolift_services.models import ManagedService
        from astrolift_services.secret_ref_config import service_organization
        from core.cluster_observability import managed_config_for

        from astrolift_workflows.activities.managed_service_lifecycle import _service_cluster

        svc = ManagedService.all_objects.select_related(
            "registered_app__organization",
            "project__organization",
            "app_environment__tenant_cluster__provider_plugin",
            "tenant_cluster__provider_plugin",
        ).get(pk=managed_service_id)
        if svc.kind != ManagedService.Kind.GRAPH_DB:
            if svc.provider_placement_identity is not None:
                raise ValueError("Spanner observed placement changed; refusing provider work")
            return function(managed_service_id, *args, **kwargs)
        cluster = _service_cluster(svc)
        if cluster is None:
            raise ValueError("Spanner placement is unknown; refusing provider work")
        resolved = resolve_managed_driver(
            cluster_plugin_slug=cluster.provider_plugin.slug, kind=svc.kind, variant=svc.variant
        )
        validate_observed_placement(svc, resolved)
        if not is_spanner(resolved):
            return function(managed_service_id, *args, **kwargs)
        cfg = placement_config(cluster)
        target = target_instance(svc, cfg)
        if not cfg.project_id:
            raise ValueError("Spanner project is unknown; refusing provider work")
        organization = service_organization(svc)
        if cluster.organization_id not in {None, organization.pk}:
            raise ValueError("Spanner cluster belongs to another organization")
        expected = (cluster.pk, cluster.provider_plugin_id, str(organization.guid), cfg.project_id, target)
        digest = hashlib.sha256(f"spanner:{cfg.project_id}:{target}".encode()).digest()[:8]
        lock_id = int.from_bytes(digest, "big", signed=True)
        with transaction.atomic():
            ManagedService.all_objects.select_for_update().get(pk=svc.pk)
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_id])
            Organization.all_objects.select_for_update().get(pk=organization.pk)
            if svc.registered_app_id:
                locked_app = RegisteredApp.all_objects.select_for_update().get(pk=svc.registered_app_id)
                AppEnvironment.all_objects.select_for_update().get(pk=svc.app_environment_id)
                if locked_app.project_id:
                    Project.all_objects.select_for_update().get(pk=locked_app.project_id)
            elif svc.project_id:
                Project.all_objects.select_for_update().get(pk=svc.project_id)
            TenantCluster.all_objects.select_for_update().get(pk=cluster.pk)
            ProviderPlugin.all_objects.select_for_update().get(pk=cluster.provider_plugin_id)
            current = ManagedService.all_objects.select_related(
                "registered_app__organization",
                "project__organization",
                "app_environment__tenant_cluster__provider_plugin",
                "tenant_cluster__provider_plugin",
            ).get(pk=svc.pk)
            current_cluster = _service_cluster(current)
            current_resolved = resolve_managed_driver(
                cluster_plugin_slug=current_cluster.provider_plugin.slug,
                kind=current.kind,
                variant=current.variant,
            )
            current_org = service_organization(current)
            if current_cluster.organization_id not in {None, current_org.pk}:
                raise ValueError("Spanner cluster belongs to another organization")
            if current.registered_app_id:
                if current.app_environment.registered_app_id != current.registered_app_id:
                    raise ValueError("Spanner environment belongs to another app")
                project = current.registered_app.project
                if project is not None and project.organization_id != current_org.pk:
                    raise ValueError("Spanner project belongs to another organization")
            if function.__name__ != "_deprovision_sync":
                ancestry = [current, current_org, current_cluster, current_cluster.provider_plugin]
                if current.registered_app_id:
                    ancestry.extend(
                        [current.registered_app, current.app_environment, current.registered_app.project]
                    )
                elif current.project_id:
                    ancestry.append(current.project)
                if not current_cluster.is_active or not current_cluster.provider_plugin.is_enabled:
                    raise ValueError("Spanner provider placement is inactive; refusing provider work")
                if any(row is not None and row.deleted_at is not None for row in ancestry):
                    raise ValueError("Spanner source ancestry is retired; refusing provider work")
            validate_observed_placement(current, current_resolved)
            raw_cfg = placement_config(current_cluster)
            if (
                current_cluster.pk,
                current_cluster.provider_plugin_id,
                str(current_org.guid),
                raw_cfg.project_id,
                target_instance(current, raw_cfg),
            ) != expected:
                raise ValueError("Spanner placement changed while waiting; refusing provider work")
            current_cfg = managed_config_for(
                current_resolved.plugin_slug, current_cluster, kind=current.kind, variant=current.variant
            )
            actual = (
                current_cluster.pk,
                current_cluster.provider_plugin_id,
                str(service_organization(current).guid),
                current_cfg.project_id,
                target_instance(current, current_cfg),
            )
            if actual != expected or current_resolved.driver_cls is not resolved.driver_cls:
                raise ValueError("Spanner placement changed while waiting; refusing provider work")
            return function(managed_service_id, *args, **kwargs)

    return guarded
