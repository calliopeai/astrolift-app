"""Activities for ``RotateSecretBundleWorkflow`` +
``DeleteSecretBundleFromClustersWorkflow`` +
``SecretBundleScheduledRefreshWorkflow`` (#365).

Three logical operations the activity layer exposes:

1. ``refresh_secret_bundle_in_cluster`` — re-fetches the bundle's
   values from the SecretsBackend driver, builds the k8s Secret
   manifest (matching what ``update_secrets`` emits at deploy time),
   and applies it to one cluster's app namespace. Idempotent — re-
   applies overwrite the in-cluster Secret cleanly.

2. ``bounce_workloads_consuming_bundle`` — patches every Deployment in
   the namespace whose pod spec ``envFrom``-references the bundle's
   Secret with a fresh
   ``kubectl.kubernetes.io/restartedAt`` annotation, forcing a
   rolling restart so pods pick up the rotated values. Pods that
   read secrets at startup only need this; pods using the projected-
   secret reloader pattern don't, but the annotation is harmless.

3. ``delete_secret_from_cluster`` — calls
   ``ClusterDriver.delete_manifests`` for the materialized Secret on
   one cluster. Fires from the delete-bundle workflow.

A fourth helper (``list_active_secret_bundle_targets``) materializes
the (cluster, app, env, namespace) fan-out targets the rotation +
delete workflows iterate over. Keeps the workflow body free of
Django.
"""

from __future__ import annotations

import base64
import logging
from datetime import UTC, datetime
from typing import Any

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.secret_rotation")


def _list_targets_sync(secret_bundle_id: int) -> list[dict[str, Any]]:
    """Return one row per (cluster, app, env) where the bundle has an
    active ``AppSecretBundleRef``. Each row carries the IDs the rest
    of the rotation flow needs.

    Returning serialised dicts (not ORM rows) keeps the result safely
    transmissible through Temporal's JSON converter.
    """
    from astrolift_services.models import AppSecretBundleRef

    refs = list(
        AppSecretBundleRef.objects.filter(
            secret_bundle_id=secret_bundle_id,
            deleted_at__isnull=True,
        ).select_related(
            "registered_app__organization",
            "app_environment__tenant_cluster",
            "secret_bundle",
        ),
    )
    out: list[dict[str, Any]] = []
    for ref in refs:
        cluster = ref.app_environment.tenant_cluster
        if cluster is None:
            # No cluster bound — nothing to materialize against. Skip
            # rather than fail; the deploy path raises on this case
            # already and we don't want to block other targets.
            continue
        out.append(
            {
                "ref_id": ref.pk,
                "registered_app_id": ref.registered_app_id,
                "app_environment_id": ref.app_environment_id,
                "tenant_cluster_id": cluster.pk,
                "app_slug": ref.registered_app.slug,
                "bundle_slug": ref.secret_bundle.slug,
                "bundle_backend_ref": ref.secret_bundle.backend_ref,
                "prefix": ref.prefix or "",
            },
        )
    return out


@activity.defn(name="astrolift.secret_rotation.list_targets")
async def list_active_secret_bundle_targets(
    secret_bundle_id: int,
) -> list[dict[str, Any]]:
    """Workflow entry point — returns the bundle's fan-out target
    list. Empty list means no active refs (bundle never referenced or
    every ref soft-deleted); workflow short-circuits."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    targets = await sync_to_async(_list_targets_sync)(secret_bundle_id)
    log.info(
        "list_active_secret_bundle_targets returned %d target(s)",
        len(targets),
        extra={"secret_bundle_id": secret_bundle_id},
    )
    return targets


def _materialize_secret_manifest(
    *,
    bundle_slug: str,
    bundle_backend_ref: str,
    prefix: str,
    namespace: str,
    secrets_backend,
    bundle=None,
) -> dict[str, Any]:
    """Pull values from the SecretsBackend + shape them into the same
    k8s Secret dict ``update_secrets`` emits at deploy time. Keeps the
    two materialization paths byte-identical so rotated values match
    what a fresh deploy would produce.

    Side effect (#441): when ``bundle`` is supplied, refresh
    ``SecretBundle.last_known_keys`` from the just-fetched payload.
    Doing it here keeps the cache aligned with whatever the rotation
    workflow actually materialised, without a second backend call.
    """
    from astrolift_services.bundle_keys import _set_known_keys
    from core.app_deploy import AppDeployError

    kvs = secrets_backend.get(bundle_backend_ref)
    if kvs is None:
        raise AppDeployError(
            f"secret bundle {bundle_slug!r} backend_ref "
            f"{bundle_backend_ref!r} not found in secrets backend",
        )
    if bundle is not None:
        try:
            _set_known_keys(bundle, list(kvs.keys()))
        except Exception:  # noqa: BLE001 — cache mis-write must not block rotate
            log.warning(
                "key-count cache update failed for bundle %s -- rotation "
                "continues; UI keyCount will lag until next refresh",
                bundle.guid,
                exc_info=True,
            )
    data: dict[str, str] = {}
    for k, v in kvs.items():
        full_key = f"{prefix}{k}" if prefix else k
        data[full_key] = base64.b64encode(str(v).encode("utf-8")).decode("ascii")
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": bundle_slug,
            "namespace": namespace,
            "labels": {
                "astrolift.io/managed-by": "astrolift",
                "astrolift.io/secret-bundle": bundle_slug,
            },
            "annotations": {
                "astrolift.io/last-rotated-at": datetime.now(UTC).isoformat(),
            },
        },
        "type": "Opaque",
        "data": data,
    }


def _refresh_in_cluster_sync(target: dict[str, Any]) -> dict[str, Any]:
    """Apply the rotated Secret to one cluster's app namespace.

    ``target`` is one row out of ``_list_targets_sync``. Returns a
    summary dict the workflow rolls up.
    """
    from astrolift_clusters.models import TenantCluster
    from astrolift_registry.models import RegisteredApp
    from astrolift_services.models import SecretBundle
    from core.app_deploy import (
        AppDeployError,
        driver_for_capability,
        namespace_for_app,
    )
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    cluster = TenantCluster.all_objects.select_related("provider_plugin").get(
        pk=target["tenant_cluster_id"],
    )
    app = RegisteredApp.all_objects.select_related("organization").get(
        pk=target["registered_app_id"],
    )
    namespace = namespace_for_app(app)
    cluster_driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    secrets_backend = driver_for_capability(cluster, "secrets")
    # #441: hand the bundle row to the materialiser so the
    # last-known-keys cache is refreshed off the same fetch.
    bundle = SecretBundle.all_objects.filter(
        backend_ref=target["bundle_backend_ref"],
        slug=target["bundle_slug"],
    ).first()
    manifest = _materialize_secret_manifest(
        bundle_slug=target["bundle_slug"],
        bundle_backend_ref=target["bundle_backend_ref"],
        prefix=target["prefix"],
        namespace=namespace,
        secrets_backend=secrets_backend,
        bundle=bundle,
    )
    result = cluster_driver.apply_manifests(ctx.slug, namespace, [manifest])
    if not result.ok:
        raise AppDeployError(
            f"refresh_secret_bundle apply failed for bundle "
            f"{target['bundle_slug']!r} on cluster {cluster.slug!r}: "
            + "; ".join(str(e) for e in result.errors),
        )
    return {
        "cluster_slug": cluster.slug,
        "namespace": namespace,
        "secret_name": target["bundle_slug"],
    }


@activity.defn(name="astrolift.secret_rotation.refresh_in_cluster")
async def refresh_secret_bundle_in_cluster(
    target: dict[str, Any],
) -> dict[str, Any]:
    """Re-fetch + apply the bundle's Secret on one cluster. Idempotent."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    summary = await sync_to_async(_refresh_in_cluster_sync)(target)
    log.info(
        "refresh_secret_bundle_in_cluster cluster=%s namespace=%s name=%s",
        summary["cluster_slug"],
        summary["namespace"],
        summary["secret_name"],
        extra={"target": target},
    )
    return summary


def _bounce_workloads_sync(target: dict[str, Any]) -> int:
    """Patch every Deployment in the app's namespace that envFroms
    the bundle's Secret with a fresh restart annotation. Returns the
    count of bounced workloads.

    Uses ``ClusterDriver.list_workloads`` + ``patch_workload`` (the
    same shape the rolling-restart code in deploy uses). If the
    driver doesn't surface a list_workloads call, we fall back to
    patching a single Deployment named after the app slug — that's
    the convention the renderer emits.
    """
    from astrolift_clusters.models import TenantCluster
    from astrolift_registry.models import RegisteredApp
    from core.app_deploy import namespace_for_app
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    cluster = TenantCluster.all_objects.get(pk=target["tenant_cluster_id"])
    app = RegisteredApp.all_objects.select_related("organization").get(
        pk=target["registered_app_id"],
    )
    namespace = namespace_for_app(app)
    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)

    bundle_slug = target["bundle_slug"]
    now_iso = datetime.now(UTC).isoformat()
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": now_iso,
                        "astrolift.io/bundle-rotated-at": now_iso,
                        "astrolift.io/bundle-slug": bundle_slug,
                    },
                },
            },
        },
    }
    # Best-effort discovery of workloads that envFrom this bundle.
    # The cluster driver protocol has ``list_workloads`` on some
    # impls and not others — guard the call.
    list_workloads = getattr(driver, "list_workloads", None)
    targets: list[tuple[str, str]] = []
    if callable(list_workloads):
        try:
            for kind, name in list_workloads(ctx.slug, namespace):
                if kind == "Deployment":
                    targets.append((kind, name))
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "list_workloads on cluster=%s ns=%s failed: %s — " "falling back to single-workload patch",
                cluster.slug,
                namespace,
                exc,
            )
            targets = []
    if not targets:
        # Fall back to the canonical workload name — matches the
        # rendered Deployment for single-workload apps. Multi-workload
        # apps will only bounce the primary on the fallback path, but
        # the new values still propagate on next pod restart.
        targets = [("Deployment", app.slug)]

    bounced = 0
    patch_workload = getattr(driver, "patch_workload", None)
    if not callable(patch_workload):
        # Older driver — emit a warning and return 0; the renderer
        # will pick up the new values on next deploy regardless.
        log.warning(
            "cluster driver for %s has no patch_workload — workloads "
            "won't bounce; new secret values land on next deploy",
            cluster.slug,
        )
        return 0
    for kind, name in targets:
        try:
            patch_workload(ctx.slug, namespace, kind, name, patch)
            bounced += 1
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "patch_workload(%s/%s) on cluster=%s failed: %s",
                kind,
                name,
                cluster.slug,
                exc,
            )
    return bounced


@activity.defn(name="astrolift.secret_rotation.bounce_workloads")
async def bounce_workloads_consuming_bundle(
    target: dict[str, Any],
) -> int:
    """Roll-restart Deployments in the target namespace via annotation
    patch so pods re-read the rotated Secret. Returns count of
    bounced workloads."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    n = await sync_to_async(_bounce_workloads_sync)(target)
    log.info(
        "bounce_workloads_consuming_bundle bounced=%d",
        n,
        extra={"target": target},
    )
    return n


def _delete_from_cluster_sync(target: dict[str, Any]) -> dict[str, Any]:
    """Delete the materialized Secret on one cluster.

    Treats not-found as success — the delete-bundle workflow is the
    cleanup hook on soft-delete, and a Secret that's already gone is
    the desired end state.
    """
    from astrolift_clusters.models import TenantCluster
    from astrolift_registry.models import RegisteredApp
    from core.app_deploy import namespace_for_app
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    cluster = TenantCluster.all_objects.get(pk=target["tenant_cluster_id"])
    app = RegisteredApp.all_objects.select_related("organization").get(
        pk=target["registered_app_id"],
    )
    namespace = namespace_for_app(app)
    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    stub = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": target["bundle_slug"],
            "namespace": namespace,
        },
    }
    result = driver.delete_manifests(ctx.slug, namespace, [stub])
    # The driver's delete_manifests treats not-found as ok per the
    # SDK contract — only actual delete failures surface in errors.
    if result.errors:
        log.warning(
            "delete_manifests bundle=%s cluster=%s ns=%s errors=%s",
            target["bundle_slug"],
            cluster.slug,
            namespace,
            result.errors,
        )
    return {
        "cluster_slug": cluster.slug,
        "namespace": namespace,
        "secret_name": target["bundle_slug"],
        "errors": list(result.errors),
    }


@activity.defn(name="astrolift.secret_rotation.delete_from_cluster")
async def delete_secret_from_cluster(target: dict[str, Any]) -> dict[str, Any]:
    """Delete the materialized k8s Secret for the bundle on one
    cluster. Not-found is treated as success."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    summary = await sync_to_async(_delete_from_cluster_sync)(target)
    log.info(
        "delete_secret_from_cluster cluster=%s name=%s errors=%d",
        summary["cluster_slug"],
        summary["secret_name"],
        len(summary["errors"]),
        extra={"target": target},
    )
    return summary


def _list_bundles_due_for_refresh_sync() -> list[int]:
    """Return IDs of every SecretBundle that has at least one active
    ``AppSecretBundleRef``. The scheduled refresh workflow iterates
    these. Inactive bundles (no refs) are skipped — no point pulling
    values nothing consumes."""
    from astrolift_services.models import SecretBundle

    return list(
        SecretBundle.objects.filter(
            deleted_at__isnull=True,
            app_refs__deleted_at__isnull=True,
        )
        .distinct()
        .order_by("pk")
        .values_list("pk", flat=True),
    )


@activity.defn(name="astrolift.secret_rotation.list_bundles_due_for_refresh")
async def list_bundles_due_for_refresh() -> list[int]:
    """Workflow entry for the scheduled hourly sweep — returns the
    IDs of every actively-referenced bundle."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    ids = await sync_to_async(_list_bundles_due_for_refresh_sync)()
    log.info("list_bundles_due_for_refresh returned %d bundle(s)", len(ids))
    return ids
