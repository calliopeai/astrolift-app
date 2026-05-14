"""
Activities for the app onboarding + deploy workflows.

These wrap the platform models behind ``@activity.defn`` so the
workflow definition stays free of Django imports (Temporal runs
workflows in a sandbox that disallows non-deterministic imports).

The bodies are deliberately thin: each activity does exactly one
durable thing. Provider-specific logic happens behind the
``astrolift_drivers`` interfaces, which we resolve at activity entry.
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities")


@activity.defn(name="astrolift.app.mark_provisioning")
async def mark_app_provisioning(registered_app_id: int) -> None:
    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.all_objects.get(pk=registered_app_id)
    if app.provisioning_status == RegisteredApp.ProvisioningStatus.PROVISIONING.value:
        return
    app.transition_provisioning(RegisteredApp.ProvisioningStatus.PROVISIONING)


@activity.defn(name="astrolift.app.mark_ready")
async def mark_app_ready(registered_app_id: int) -> None:
    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.all_objects.get(pk=registered_app_id)
    app.transition_provisioning(RegisteredApp.ProvisioningStatus.READY)


def _provision_namespace_sync(registered_app_id: int, app_environment_id: int | None) -> str:
    from astrolift_clusters.models import TenantCluster
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp
    from core.app_deploy import AppDeployError, namespace_for_app
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    app = RegisteredApp.all_objects.select_related("organization").get(pk=registered_app_id)
    cluster: TenantCluster | None = None
    if app_environment_id is not None:
        env = AppEnvironment.all_objects.select_related("tenant_cluster").get(pk=app_environment_id)
        cluster = env.tenant_cluster
    if cluster is None:
        # Fall back: the app may have a single environment, in which case
        # we provision in that cluster. If there are zero or multiple
        # environments the caller must pass app_environment_id.
        envs = list(
            AppEnvironment.objects.filter(registered_app=app, deleted_at__isnull=True).select_related(
                "tenant_cluster",
            ),
        )
        if len(envs) != 1:
            raise AppDeployError(
                f"app {app.slug!r} has {len(envs)} environments; pass app_environment_id explicitly",
            )
        cluster = envs[0].tenant_cluster
    if cluster is None:
        raise AppDeployError(f"app {app.slug!r} env has no tenant_cluster bound")
    namespace = namespace_for_app(app)
    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    labels = {
        "astrolift.io/managed-by": "astrolift",
        "astrolift.io/organization": app.organization.slug,
        "astrolift.io/app": app.slug,
    }
    annotations = {
        "astrolift.io/registered-app-id": str(app.pk),
    }
    driver.ensure_namespace(ctx.slug, namespace, labels, annotations)
    return namespace


@activity.defn(name="astrolift.app.provision_namespace")
async def provision_namespace(registered_app_id: int, app_environment_id: int | None = None) -> None:
    """Create the app's Kubernetes namespace on its bound cluster.

    Idempotent — ``driver.ensure_namespace`` server-side-applies labels +
    annotations so re-runs reconcile rather than fail. The labels carry
    organization + app slug so cluster-wide queries can scope to
    astrolift-managed namespaces.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    namespace = await sync_to_async(_provision_namespace_sync)(registered_app_id, app_environment_id)
    log.info(
        "provision_namespace ensured namespace=%s",
        namespace,
        extra={"registered_app_id": registered_app_id, "app_environment_id": app_environment_id},
    )


@activity.defn(name="astrolift.app.provision_registry_repo")
async def provision_registry_repo(registered_app_id: int) -> str:
    activity.heartbeat()
    log.info("provision_registry_repo placeholder", extra={"registered_app_id": registered_app_id})
    return ""


@activity.defn(name="astrolift.app.provision_managed_services_initial")
async def provision_managed_services_initial(
    registered_app_id: int,
    app_environment_id: int,
) -> list[int]:
    log.info(
        "provision_managed_services_initial placeholder",
        extra={"registered_app_id": registered_app_id, "app_environment_id": app_environment_id},
    )
    return []


def _pre_flight_sync(deployment_id: int) -> None:
    from astrolift_lifecycle.models import Deployment
    from core.app_deploy import AppDeployError, cluster_for_deployment

    d = Deployment.all_objects.select_related("registered_app__organization", "app_environment").get(
        pk=deployment_id,
    )
    if not d.image_tag:
        raise AppDeployError(f"deployment {deployment_id} has no image_tag set")
    app = d.registered_app
    if not (app.manifest_raw or "").strip():
        raise AppDeployError(
            f"app {app.slug!r} has no saved manifest — open the Manifest tab or re-run the registration wizard",
        )
    # Probe the cluster is bound + reachable shape; the actual auth
    # check happens at apply time when the driver opens a real client.
    cluster = cluster_for_deployment(d)
    if cluster.lifecycle != cluster.Lifecycle.MANAGED.value:
        raise AppDeployError(
            f"cluster {cluster.slug!r} is in lifecycle {cluster.lifecycle!r}, not managed — "
            "bring it into management before deploying",
        )


@activity.defn(name="astrolift.deploy.pre_flight")
async def pre_flight(deployment_id: int) -> None:
    """Read-only validation gate before any cluster mutation.

    Catches the obvious "you can't deploy because X" cases up front so
    operators get a clear pre-deploy error rather than an opaque apply
    failure. Validates: deployment has an image_tag, app has a saved
    manifest, env is bound to a cluster, and the cluster is in the
    ``managed`` lifecycle.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_pre_flight_sync)(deployment_id)


@activity.defn(name="astrolift.deploy.mark_deploying")
async def mark_deploying(deployment_id: int) -> None:
    from astrolift_lifecycle.models import Deployment

    d = Deployment.all_objects.get(pk=deployment_id)
    d.transition_to(Deployment.Status.DEPLOYING)


@activity.defn(name="astrolift.deploy.render_manifests")
async def render_manifests(deployment_id: int) -> dict[str, Any]:
    """Build the list of Kubernetes resource dicts for ``apply_manifests``.

    Reads the Deployment row, normalizes the registered-app's stored
    manifest, and runs the pure renderer in
    ``astrolift_manifest.render``. The rendered output is returned as
    ``{"resources": [...]}`` so the workflow has a single-key Temporal
    payload (stable across renderer evolution) and so ``apply_manifests``
    can pick up the same shape unchanged.
    """
    from asgiref.sync import sync_to_async

    from astrolift_lifecycle.models import Deployment
    from astrolift_manifest.normalize import NormalizationDefaults, normalize
    from astrolift_manifest.parser import parse_raw
    from astrolift_manifest.render import render_manifests as _render

    activity.heartbeat()

    def _gather():
        d = Deployment.all_objects.select_related("registered_app", "app_environment").get(pk=deployment_id)
        app = d.registered_app
        env = d.app_environment
        # Render off the stored TOML — never re-fetch from the repo at
        # apply time so deployments are reproducible after force-pushes.
        manifest = normalize(parse_raw(app.manifest_raw), defaults=NormalizationDefaults())
        return manifest, app, env, d

    manifest, app, env, d = await sync_to_async(_gather)()

    namespace = app.k8s_namespace or f"{app.organization.slug}-{app.slug}"
    resources = _render(
        manifest,
        namespace=namespace,
        image_tag=d.image_tag or "latest",
        image_repository=app.registry_repo_uri or app.slug,
        environment_name=env.name,
    )
    log.info(
        "render_manifests produced %d resource(s)",
        len(resources),
        extra={"deployment_id": deployment_id},
    )
    return {"resources": resources}


def _apply_manifests_sync(deployment_id: int) -> dict[str, list[str]]:
    from astrolift_lifecycle.models import Deployment
    from core.app_deploy import (
        AppDeployError,
        driver_for_deployment,
        render_resources_for_deployment,
    )

    d = Deployment.all_objects.select_related(
        "registered_app__organization",
        "app_environment__tenant_cluster",
    ).get(pk=deployment_id)
    driver, ctx, namespace = driver_for_deployment(d)
    resources = render_resources_for_deployment(d)
    if not resources:
        raise AppDeployError(
            f"manifest for app {d.registered_app.slug!r} rendered to zero resources — "
            "check the workloads/services block in astrolift.toml",
        )
    result = driver.apply_manifests(ctx.slug, namespace, resources)
    if not result.ok:
        raise AppDeployError(
            f"apply_manifests failed for deployment {deployment_id}: " + "; ".join(result.errors),
        )
    return {
        "created": list(result.created),
        "updated": list(result.updated),
        "unchanged": list(result.unchanged),
    }


@activity.defn(name="astrolift.deploy.apply_manifests")
async def apply_manifests(deployment_id: int) -> dict[str, list[str]]:
    """Apply the rendered manifest set to the deployment's cluster.

    Uses server-side apply (idempotent) via the cluster driver. Returns
    the ``ApplyResult`` shape ({created/updated/unchanged}) so the
    workflow event log carries which resources actually changed for an
    operator-facing diff view.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    summary = await sync_to_async(_apply_manifests_sync)(deployment_id)
    log.info(
        "apply_manifests created=%d updated=%d unchanged=%d",
        len(summary["created"]),
        len(summary["updated"]),
        len(summary["unchanged"]),
        extra={"deployment_id": deployment_id},
    )
    return summary


@activity.defn(name="astrolift.deploy.update_secrets")
async def update_secrets(deployment_id: int) -> None:
    log.info("update_secrets placeholder", extra={"deployment_id": deployment_id})


@activity.defn(name="astrolift.deploy.wait_dns")
async def wait_dns(deployment_id: int) -> None:
    activity.heartbeat()


def _poll_rollout_sync(deployment_id: int, timeout_seconds: int) -> bool:
    from astrolift_lifecycle.models import Deployment
    from core.app_deploy import (
        AppDeployError,
        driver_for_deployment,
        render_resources_for_deployment,
        workloads_from_resources,
    )

    d = Deployment.all_objects.select_related(
        "registered_app__organization",
        "app_environment__tenant_cluster",
    ).get(pk=deployment_id)
    driver, ctx, namespace = driver_for_deployment(d)
    resources = render_resources_for_deployment(d)
    workloads = workloads_from_resources(resources)
    if not workloads:
        # Pure-service manifests (no Deployments/StatefulSets/DaemonSets)
        # are trivially "rolled out" the moment apply_manifests returns.
        return True
    for kind, name in workloads:
        result = driver.poll_rollout(ctx.slug, namespace, kind, name, timeout_seconds)
        if not result.success:
            raise AppDeployError(
                f"rollout {kind}/{name} in {namespace} failed: {result.message}"
                + (" (timed out)" if result.timed_out else ""),
            )
    return True


@activity.defn(name="astrolift.deploy.poll_rollout")
async def poll_rollout(deployment_id: int, timeout_seconds: int = 600) -> bool:
    """Wait for every Deployment / StatefulSet / DaemonSet in the
    rendered manifest set to roll out successfully.

    Driven by the cluster driver's ``poll_rollout`` which ticks at least
    every 15s. Activity timeout in the workflow caps the overall window;
    ``timeout_seconds`` is the per-workload cap (default 10 min, matches
    the ClusterDriver protocol default).
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_poll_rollout_sync)(deployment_id, timeout_seconds)


def _health_check_sync(deployment_id: int) -> bool:
    from astrolift_lifecycle.models import Deployment
    from core.app_deploy import (
        AppDeployError,
        driver_for_deployment,
        render_resources_for_deployment,
        workloads_from_resources,
    )

    d = Deployment.all_objects.select_related(
        "registered_app__organization",
        "app_environment__tenant_cluster",
    ).get(pk=deployment_id)
    driver, ctx, namespace = driver_for_deployment(d)
    resources = render_resources_for_deployment(d)
    workloads = workloads_from_resources(resources)
    if not workloads:
        return True
    for kind, name in workloads:
        status = driver.get_workload_status(ctx.slug, namespace, kind, name)
        if status.ready_replicas < status.desired_replicas:
            raise AppDeployError(
                f"workload {kind}/{name} in {namespace} not healthy: "
                f"ready={status.ready_replicas} desired={status.desired_replicas}",
            )
    return True


@activity.defn(name="astrolift.deploy.health_check")
async def health_check(deployment_id: int) -> bool:
    """Post-rollout readiness check.

    Cross-verifies what ``poll_rollout`` saw — every workload's
    ``ready_replicas`` should equal its ``desired_replicas`` at this
    point. Catches edge cases where rollout returns success but a pod
    flapped in the brief window before we sampled.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_health_check_sync)(deployment_id)


@activity.defn(name="astrolift.deploy.mark_running")
async def mark_running(deployment_id: int) -> None:
    from astrolift_lifecycle.models import Deployment

    d = Deployment.all_objects.get(pk=deployment_id)
    d.transition_to(Deployment.Status.RUNNING)


def _create_rollback_deployment_sync(deployment_id: int) -> int:
    """Sync core of ``create_rollback_deployment``. Exposed as a
    plain function so unit tests can call it directly — sync_to_async
    in pytest-django lands in a different DB connection that the
    transactional rollback doesn't see.
    """
    from astrolift_lifecycle.models import Deployment

    current = Deployment.all_objects.select_related("registered_app", "app_environment").get(pk=deployment_id)
    # Find the prior running deployment in the same env (older
    # than the current row). Pick the most recent terminal-OK one.
    prior = (
        Deployment.objects.filter(
            registered_app=current.registered_app,
            app_environment=current.app_environment,
            deleted_at__isnull=True,
            created_at__lt=current.created_at,
            status__in=[
                Deployment.Status.RUNNING.value,
                Deployment.Status.SUPERSEDED.value,
            ],
        )
        .order_by("-created_at")
        .first()
    )
    if prior is None:
        raise RuntimeError(
            f"deployment {deployment_id} has no prior running revision in this env to roll back to"
        )
    new_deploy = Deployment.objects.create(
        registered_app=current.registered_app,
        app_environment=current.app_environment,
        workload_id=prior.workload_id,
        triggered_by_user_id=current.triggered_by_user_id,
        trigger_kind=Deployment.TriggerKind.ROLLBACK.value,
        status=Deployment.Status.PENDING.value,
        image_tag=prior.image_tag,
        image_digest=prior.image_digest,
        config_snapshot=prior.config_snapshot,
        approvals_required=0,
        approvals_received=0,
        ci_actor_kind="rollback",
        commit_sha=prior.commit_sha,
        branch=prior.branch,
        ci_provider=prior.ci_provider,
    )
    # Current row was the bad deploy — mark it superseded so the
    # state machine shows the lineage.
    try:
        current.transition_to(Deployment.Status.SUPERSEDED)
    except ValueError:
        # Already in a terminal state — no-op.
        pass
    return new_deploy.pk


@activity.defn(name="astrolift.deploy.create_rollback_deployment")
async def create_rollback_deployment(deployment_id: int) -> int:
    """Resolve the prior ``running`` deployment in the same env and
    create a new ``rollback`` deployment row that copies its image
    tag + config snapshot. Returns the new deployment id; the
    workflow then runs the normal apply path against it.
    """
    from asgiref.sync import sync_to_async

    return await sync_to_async(_create_rollback_deployment_sync)(deployment_id)


def _create_promotion_deployment_sync(
    source_deployment_id: int,
    target_app_environment_id: int,
) -> int:
    """Sync core of ``create_promotion_deployment``. See
    ``_create_rollback_deployment_sync`` for the rationale."""
    from astrolift_lifecycle.models import AppEnvironment, Deployment

    source = Deployment.all_objects.select_related("registered_app", "app_environment").get(
        pk=source_deployment_id
    )
    target_env = AppEnvironment.objects.get(pk=target_app_environment_id)
    if target_env.registered_app_id != source.registered_app_id:
        raise RuntimeError("promotion target must belong to the same app as the source")
    if target_env.deploys_paused:
        raise RuntimeError(f"target environment {target_env.name!r} has deploys paused")

    initial_status = (
        Deployment.Status.PENDING_APPROVAL.value
        if target_env.required_approvals > 0
        else Deployment.Status.PENDING.value
    )
    new_deploy = Deployment.objects.create(
        registered_app=source.registered_app,
        app_environment=target_env,
        workload_id=source.workload_id,
        triggered_by_user_id=source.triggered_by_user_id,
        trigger_kind=Deployment.TriggerKind.PROMOTION.value,
        status=initial_status,
        image_tag=source.image_tag,
        image_digest=source.image_digest,
        config_snapshot=source.config_snapshot,
        promoted_from=source,
        approvals_required=target_env.required_approvals,
        approvals_received=0,
        ci_actor_kind=source.ci_actor_kind or "promotion",
        commit_sha=source.commit_sha,
        branch=source.branch,
        ci_provider=source.ci_provider,
    )
    return new_deploy.pk


@activity.defn(name="astrolift.deploy.create_promotion_deployment")
async def create_promotion_deployment(
    source_deployment_id: int,
    target_app_environment_id: int,
) -> int:
    """Move an image tag from source env → target env on the same
    app. The new row stamps ``promoted_from`` so the lineage is
    queryable. Approvals on the target env are honoured by setting
    the same initial status logic the deploy mutation uses.
    """
    from asgiref.sync import sync_to_async

    return await sync_to_async(_create_promotion_deployment_sync)(
        source_deployment_id, target_app_environment_id
    )
