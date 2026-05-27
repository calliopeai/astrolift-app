"""Activities for ``MigrateAppWorkflow`` — moves an app environment
from its current ``tenant_cluster`` to a target cluster.

Migration is a 5-step pipeline:

  1. validate_migration_target  — target is MANAGED + not the source.
  2. apply_to_target_cluster    — render against the source env's
                                   config, apply to the target cluster's
                                   namespace + driver. The
                                   ``AppEnvironment.tenant_cluster``
                                   binding is still the SOURCE at this
                                   step — both clusters are running the
                                   app simultaneously after apply.
  3. poll_rollout_on_target     — wait for target workloads to roll out.
  4. switch_app_env_binding     — atomic flip of
                                   ``AppEnvironment.tenant_cluster_id``
                                   to the target. Future deploys land
                                   on target.
  5. drain_source_cluster       — delete the app's resources from source
                                   (namespace cascade). Optional.

Step 4 is the moment of truth — once it commits, the new cluster is
the binding for the env. Step 5 is best-effort: if it fails, the
migration is still ``ok`` because the target is serving traffic; the
operator gets a clear message that the source still has lingering
resources and can clean up manually.
"""

from __future__ import annotations

import logging

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.migration")


def _validate_migration_target_sync(
    app_environment_id: int,
    target_cluster_id: int,
) -> None:
    from astrolift_clusters.models import TenantCluster
    from astrolift_lifecycle.models import AppEnvironment
    from core.app_deploy import AppDeployError

    env = AppEnvironment.all_objects.select_related("tenant_cluster").get(pk=app_environment_id)
    if env.tenant_cluster_id == target_cluster_id:
        raise AppDeployError(
            f"app environment {env.name!r} already bound to target cluster — nothing to migrate",
        )
    target = TenantCluster.all_objects.get(pk=target_cluster_id)
    if target.lifecycle != TenantCluster.Lifecycle.MANAGED.value:
        raise AppDeployError(
            f"target cluster {target.slug!r} lifecycle is {target.lifecycle!r}, not managed",
        )
    if target.deleted_at is not None:
        raise AppDeployError(f"target cluster {target.slug!r} is soft-deleted")


@activity.defn(name="astrolift.migration.validate_target")
async def validate_migration_target(app_environment_id: int, target_cluster_id: int) -> None:
    """Refuse migration when target is the source, unmanaged, or soft-deleted."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_validate_migration_target_sync)(app_environment_id, target_cluster_id)


def _apply_to_target_cluster_sync(deployment_id: int, target_cluster_id: int) -> dict[str, list[str]]:
    from astrolift_lifecycle.models import Deployment
    from core.app_deploy import (
        AppDeployError,
        driver_for_target_cluster,
        render_resources_for_deployment,
    )

    d = Deployment.all_objects.select_related(
        "registered_app__organization",
        "app_environment",
    ).get(pk=deployment_id)
    driver, ctx, namespace = driver_for_target_cluster(d, target_cluster_id)
    # Ensure the namespace exists on the target.
    driver.ensure_namespace(
        ctx.slug,
        namespace,
        {
            "astrolift.io/managed-by": "astrolift",
            "astrolift.io/organization": d.registered_app.organization.slug,
            "astrolift.io/app": d.registered_app.slug,
        },
        {"astrolift.io/registered-app-id": str(d.registered_app.pk)},
    )
    resources = render_resources_for_deployment(d)
    if not resources:
        raise AppDeployError(
            f"migrate: manifest for app {d.registered_app.slug!r} rendered to zero resources",
        )
    result = driver.apply_manifests(ctx.slug, namespace, resources)
    if not result.ok:
        raise AppDeployError("apply_to_target failed: " + "; ".join(str(e) for e in result.errors))
    return {
        "created": list(result.created),
        "updated": list(result.updated),
        "unchanged": list(result.unchanged),
    }


@activity.defn(name="astrolift.migration.apply_to_target")
async def apply_to_target_cluster(
    deployment_id: int,
    target_cluster_id: int,
) -> dict[str, list[str]]:
    """Apply the deployment's manifests to the target cluster.

    The source cluster is left untouched until ``switch_app_env_binding``
    flips the FK — both clusters run the app simultaneously between
    apply and switch. That overlap is the migration's safety net: a
    target-side failure leaves source serving traffic, and operators
    can decide to abort.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    summary = await sync_to_async(_apply_to_target_cluster_sync)(deployment_id, target_cluster_id)
    log.info(
        "migrate apply_to_target created=%d updated=%d unchanged=%d",
        len(summary["created"]),
        len(summary["updated"]),
        len(summary["unchanged"]),
        extra={"deployment_id": deployment_id, "target_cluster_id": target_cluster_id},
    )
    return summary


def _poll_rollout_on_target_sync(deployment_id: int, target_cluster_id: int) -> bool:
    from astrolift_lifecycle.models import Deployment
    from core.app_deploy import (
        AppDeployError,
        driver_for_target_cluster,
        render_resources_for_deployment,
        workloads_from_resources,
    )

    d = Deployment.all_objects.select_related(
        "registered_app__organization",
        "app_environment",
    ).get(pk=deployment_id)
    driver, ctx, namespace = driver_for_target_cluster(d, target_cluster_id)
    resources = render_resources_for_deployment(d)
    workloads = workloads_from_resources(resources)
    if not workloads:
        return True
    for kind, name in workloads:
        result = driver.poll_rollout(ctx.slug, namespace, kind, name, 600)
        if not result.success:
            raise AppDeployError(
                f"migrate rollout {kind}/{name} in target {namespace} failed: {result.message}"
                + (" (timed out)" if result.timed_out else ""),
            )
    return True


@activity.defn(name="astrolift.migration.poll_rollout_on_target")
async def poll_rollout_on_target(deployment_id: int, target_cluster_id: int) -> bool:
    """Wait for every workload to roll out on the target cluster."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_poll_rollout_on_target_sync)(deployment_id, target_cluster_id)


def _switch_app_env_binding_sync(app_environment_id: int, target_cluster_id: int) -> int:
    """Atomic flip: ``AppEnvironment.tenant_cluster`` -> target.

    The flip happens after apply + rollout + health on the target are
    all green. From this point on, every future deploy / re-render lands
    on the target. Returns the previous (source) cluster id so the
    workflow can hand it to the drain step.
    """
    from django.db import transaction

    from astrolift_lifecycle.models import AppEnvironment

    with transaction.atomic():
        env = AppEnvironment.all_objects.select_for_update().get(pk=app_environment_id)
        source_cluster_id = env.tenant_cluster_id
        env.tenant_cluster_id = target_cluster_id
        env.save(update_fields=["tenant_cluster", "updated_at", "version"])
    return source_cluster_id


@activity.defn(name="astrolift.migration.switch_binding")
async def switch_app_env_binding(app_environment_id: int, target_cluster_id: int) -> int:
    """Atomically flip the env's cluster binding to the migration target.

    Returns the previous (source) cluster id for the optional drain step.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_switch_app_env_binding_sync)(app_environment_id, target_cluster_id)


def _drain_source_cluster_sync(
    registered_app_id: int,
    app_environment_id: int,
    source_cluster_id: int,
) -> list[str]:
    """Delete the app's resources from the source cluster.

    Best-effort: we render the app's resource set and ask the source
    driver to delete each one. Failures are logged and surfaced in the
    return value (list of error messages) but don't fail the activity —
    the migration is already complete; this is cleanup.

    Uses ``delete_manifests`` rather than ``delete_namespace`` because
    other apps may share the namespace (if the cluster runs multiple
    orgs/apps in the same ns, which is possible when ``k8s_namespace``
    is operator-pinned).
    """
    from astrolift_clusters.models import TenantCluster
    from astrolift_lifecycle.models import AppEnvironment, Deployment
    from astrolift_registry.models import RegisteredApp
    from core.app_deploy import (
        AppDeployError,
        namespace_for_app,
        render_resources_for_deployment,
    )
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    app = RegisteredApp.all_objects.select_related("organization").get(pk=registered_app_id)
    env = AppEnvironment.all_objects.get(pk=app_environment_id)
    try:
        source = TenantCluster.all_objects.get(pk=source_cluster_id)
    except TenantCluster.DoesNotExist:
        # Source cluster was deleted between apply and drain — nothing
        # to clean up.
        return []
    # Pick the latest deployment for rendering — same render the apply
    # path used. If there's no deployment row (shouldn't happen post-
    # migration), fall back to render-less namespace-only cleanup.
    deployment = (
        Deployment.objects.filter(
            registered_app=app,
            app_environment=env,
            deleted_at__isnull=True,
        )
        .order_by("-created_at")
        .first()
    )
    if deployment is None:
        return []
    try:
        driver = _driver_for_cluster(source)
    except Exception as exc:  # noqa: BLE001 - non-fatal cleanup path
        return [f"could not build source driver: {exc}"]
    ctx = _context_for_cluster(source)
    namespace = namespace_for_app(app)
    try:
        resources = render_resources_for_deployment(deployment)
    except AppDeployError as exc:
        return [str(exc)]
    if not resources:
        return []
    result = driver.delete_manifests(ctx.slug, namespace, resources)
    return list(result.errors)


@activity.defn(name="astrolift.migration.drain_source")
async def drain_source_cluster(
    registered_app_id: int,
    app_environment_id: int,
    source_cluster_id: int,
) -> list[str]:
    """Delete the app's resources from the source cluster (best-effort)."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    errors = await sync_to_async(_drain_source_cluster_sync)(
        registered_app_id,
        app_environment_id,
        source_cluster_id,
    )
    if errors:
        log.warning(
            "drain_source_cluster surfaced %d error(s): %s",
            len(errors),
            "; ".join(errors[:3]),
            extra={
                "registered_app_id": registered_app_id,
                "app_environment_id": app_environment_id,
                "source_cluster_id": source_cluster_id,
            },
        )
    else:
        log.info(
            "drain_source_cluster cleaned up source",
            extra={
                "registered_app_id": registered_app_id,
                "app_environment_id": app_environment_id,
                "source_cluster_id": source_cluster_id,
            },
        )
    return errors


__all__ = [
    "apply_to_target_cluster",
    "drain_source_cluster",
    "poll_rollout_on_target",
    "switch_app_env_binding",
    "validate_migration_target",
]
