"""Activities for ``TearDownAppWorkflow`` (#358).

Per-step durable units that match the policy module's
``TeardownStep`` enum (see ``astrolift_workflows.app_teardown``).

Step coverage in this pass:
  - mark_app_tearing_down + mark_app_deregistered (state flips)
  - list_app_managed_service_ids (fan-out plan source)
  - delete_app_namespaces (cascade-deletes app's k8s namespace
    on each cluster the app is deployed to)
  - revoke_app_deploy_tokens (soft-delete tokens so cron jobs and
    CI configurations fail loudly rather than silently)
  - soft_delete_app_records (final soft-delete of the platform
    rows per the soft-delete invariant in the policy module)

Per-capability cleanup (DNS records, IRSA / WI / FI roles, ECR /
GAR / ACR repos, TLS certs) lives on the per-capability decommission
tickets (#354 + per-cloud lifecycle epics) and composes in via
additional activities the workflow will call once those land. The
workflow shipped here is the orchestration skeleton; missing steps
are soft-no-ops so an app teardown today still removes the runtime
resources it knows how to remove.
"""

from __future__ import annotations

import logging

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.app_teardown")


def _mark_state_sync(registered_app_id: int, status: str) -> None:
    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.all_objects.get(pk=registered_app_id)
    app.transition_provisioning(
        RegisteredApp.ProvisioningStatus(status),
    )


def _mark_tearing_down_sync(registered_app_id: int) -> bool:
    """Flip the app to TEARING_DOWN. Returns True when the app was ALREADY
    in a teardown state (tearing_down, or the terminal deregistered) before
    this call — i.e. this run is a RESUME / re-trigger, not an operator's
    first destructive click.

    The caller uses that flag to skip the operator-cancel grace window on a
    resume (#1034): the window's sole purpose is to let an operator undo an
    accidental FIRST teardown, and re-waiting it on every re-trigger stalls
    finalization. Each re-trigger restarts the workflow (TERMINATE_IF_RUNNING),
    so re-applying the full 5-minute window would let back-to-back re-triggers
    reset the timer indefinitely and the stuck rows would never finalize.
    """
    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.all_objects.get(pk=registered_app_id)
    # A deregister re-fired to resume a teardown that already reached the
    # terminal DEREGISTERED state (rows soft-deleted on the prior run) must
    # be a clean no-op — DEREGISTERED → TEARING_DOWN is not a legal
    # transition and would raise, blowing up an otherwise-idempotent resume
    # at the very first step (#1034). tearing_down → tearing_down idempotency
    # is already handled inside transition_provisioning (#1006).
    if app.provisioning_status == RegisteredApp.ProvisioningStatus.DEREGISTERED:
        return True
    already_tearing_down = app.provisioning_status == RegisteredApp.ProvisioningStatus.TEARING_DOWN
    app.transition_provisioning(RegisteredApp.ProvisioningStatus.TEARING_DOWN)
    return already_tearing_down


@activity.defn(name="astrolift.app.mark_tearing_down")
async def mark_app_tearing_down(registered_app_id: int) -> bool:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_mark_tearing_down_sync)(registered_app_id)


@activity.defn(name="astrolift.app.mark_deregistered")
async def mark_app_deregistered(registered_app_id: int) -> None:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_state_sync)(
        registered_app_id,
        "deregistered",
    )


def _list_managed_service_ids_sync(registered_app_id: int) -> list[int]:
    from astrolift_services.models import ManagedService

    return list(
        ManagedService.objects.filter(
            registered_app_id=registered_app_id,
            deleted_at__isnull=True,
        ).values_list("pk", flat=True),
    )


@activity.defn(name="astrolift.app.list_managed_service_ids")
async def list_app_managed_service_ids(
    registered_app_id: int,
) -> list[int]:
    """Returns the PK list of every active ``ManagedService`` row
    bound to the app. The workflow fans out one
    ``DeprovisionManagedServiceWorkflow`` per id."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    ids = await sync_to_async(_list_managed_service_ids_sync)(
        registered_app_id,
    )
    log.info(
        "list_app_managed_service_ids found %d service(s)",
        len(ids),
        extra={"registered_app_id": registered_app_id},
    )
    return ids


def _delete_app_namespaces_sync(registered_app_id: int) -> list[str]:
    """Delete the k8s namespace per (app, env) for every environment
    the app is bound to. Cascades all Deployments / Services /
    Ingresses / ConfigMaps / Secrets / PVCs the renderer ever
    produced.

    Idempotent: if the namespace is already absent the driver's
    delete_namespace must short-circuit cleanly (per the
    ClusterDriver Protocol contract).
    """
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp
    from core.cluster_management import (
        _context_for_cluster,  # type: ignore[attr-defined]
        _driver_for_cluster,  # type: ignore[attr-defined]
    )

    app = RegisteredApp.all_objects.get(pk=registered_app_id)
    envs = list(
        AppEnvironment.objects.filter(
            registered_app=app,
            deleted_at__isnull=True,
        ).select_related("tenant_cluster__provider_plugin"),
    )
    deleted: list[str] = []
    for env in envs:
        cluster = env.tenant_cluster
        if cluster is None:
            continue
        namespace = app.k8s_namespace or (f"{app.organization.slug}-{app.slug}")
        try:
            driver = _driver_for_cluster(cluster)
            ctx = _context_for_cluster(cluster)
            driver.delete_namespace(ctx.slug, namespace, wait=True)
            deleted.append(f"{cluster.slug}/{namespace}")
        except Exception as exc:  # noqa: BLE001 — log + continue
            log.warning(
                "delete_namespace failed for app=%s cluster=%s ns=%s: %s",
                app.slug,
                cluster.slug,
                namespace,
                exc,
            )
    return deleted


@activity.defn(name="astrolift.app.delete_namespaces")
async def delete_app_namespaces(registered_app_id: int) -> list[str]:
    """Delete the app's namespace on every cluster it's deployed to.

    Per-cluster failures are logged + skipped so a single
    unreachable cluster doesn't strand the teardown. The workflow
    can retry the activity to converge; reports list the namespaces
    that did get deleted for the operator audit trail.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    deleted = await sync_to_async(_delete_app_namespaces_sync)(
        registered_app_id,
    )
    log.info(
        "delete_app_namespaces cleaned %d namespace(s)",
        len(deleted),
        extra={"registered_app_id": registered_app_id},
    )
    return deleted


def _abort_in_flight_deploys_sync(registered_app_id: int) -> list[str]:
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp
    from astrolift_workflows.client import signal_workflow

    app = RegisteredApp.all_objects.get(pk=registered_app_id)
    envs = list(
        AppEnvironment.objects.filter(
            registered_app=app,
            deleted_at__isnull=True,
        ),
    )
    signalled: list[str] = []
    for env in envs:
        # Workflow id is deterministic from app.guid/env.guid — the same id
        # the deploy mutation/CLI/cron all use.
        wf_id = f"DeployAppWorkflow-{app.guid}-{env.guid}"
        try:
            # Graceful abort: DeployAppWorkflow checks _abort_requested before
            # poll_rollout and returns ok=False. signal_workflow returns False
            # when the workflow isn't running (or Temporal is disabled), so a
            # False just means "no in-flight deploy here" — we don't terminate
            # a speculative id. A deploy ALREADY inside poll_rollout won't see
            # the flag mid-activity; the #1004 bounded retry + the namespace
            # delete that follows are the backstop for that case.
            if signal_workflow(wf_id, "abort"):
                signalled.append(wf_id)
        except Exception:  # noqa: BLE001 — best-effort; teardown proceeds
            log.warning("abort_in_flight_deploys: abort failed for %s", wf_id, exc_info=True)
    return signalled


@activity.defn(name="astrolift.app.abort_in_flight_deploys")
async def abort_in_flight_deploys(registered_app_id: int) -> list[str]:
    """Abort any in-flight DeployAppWorkflow for the app's environments
    BEFORE the namespace cascade — so a running deploy sees the abort flag
    and exits cleanly instead of polling a deleted namespace until timeout
    (#1004). Best-effort per env; returns the workflow ids actually signalled.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    signalled = await sync_to_async(_abort_in_flight_deploys_sync)(registered_app_id)
    log.info(
        "abort_in_flight_deploys signalled %d in-flight deploy(s)",
        len(signalled),
        extra={"registered_app_id": registered_app_id},
    )
    return signalled


def _revoke_app_deploy_tokens_sync(registered_app_id: int) -> int:
    """Soft-delete active deploy tokens for the app.

    Important per the policy doc: revocation must be loud — CI
    configs or cron jobs pointed at this app should fail visibly
    rather than silently. Soft-delete (vs. token rotation) is the
    right primitive: the token's lookup path raises a clear
    DeployTokenNotFound at the auth layer.
    """
    from datetime import UTC, datetime

    try:
        from astrolift_identity.models import DeployToken  # type: ignore
    except ImportError:
        # Identity app may not be loaded in every backend slice;
        # treat the absence of the model as zero tokens to revoke.
        return 0

    now = datetime.now(UTC)
    qs = DeployToken.objects.filter(
        registered_app_id=registered_app_id,
        deleted_at__isnull=True,
    )
    count = qs.count()
    for token in qs:
        token.deleted_at = now
        token.save(update_fields=["deleted_at", "updated_at", "version"])
    return count


@activity.defn(name="astrolift.app.revoke_deploy_tokens")
async def revoke_app_deploy_tokens(registered_app_id: int) -> int:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    n = await sync_to_async(_revoke_app_deploy_tokens_sync)(
        registered_app_id,
    )
    log.info(
        "revoke_app_deploy_tokens revoked %d token(s)",
        n,
        extra={"registered_app_id": registered_app_id},
    )
    return n


def _soft_delete_app_records_sync(registered_app_id: int) -> dict[str, int]:
    """Final soft-delete pass. Walks the SOFT_DELETE record kinds
    catalog from the policy module and soft-deletes the app's
    associated rows. The audit / event log kinds are intentionally
    NOT touched (they're the record of what happened).
    """
    from datetime import UTC, datetime

    from astrolift_lifecycle.models import AppEnvironment, Deployment
    from astrolift_registry.models import RegisteredApp, Workload
    from astrolift_services.models import AppSecretBundleRef

    now = datetime.now(UTC)
    summary: dict[str, int] = {}

    def _soft_delete_queryset(qs, label):
        n = 0
        for row in qs:
            if row.deleted_at is None:
                row.deleted_at = now
                row.save(
                    update_fields=["deleted_at", "updated_at", "version"],
                )
                n += 1
        summary[label] = n

    _soft_delete_queryset(
        Deployment.objects.filter(
            registered_app_id=registered_app_id,
            deleted_at__isnull=True,
        ),
        "deployments",
    )
    _soft_delete_queryset(
        AppSecretBundleRef.objects.filter(
            registered_app_id=registered_app_id,
            deleted_at__isnull=True,
        ),
        "secret_bundle_refs",
    )
    _soft_delete_queryset(
        AppEnvironment.objects.filter(
            registered_app_id=registered_app_id,
            deleted_at__isnull=True,
        ),
        "app_environments",
    )
    # Workloads (incl. agent Workloads) — #1012: these were NOT soft-deleted,
    # so an agent's business record outlived its RegisteredApp (the K8s
    # cascade removed the runtime objects, but the row survived). Soft-delete
    # so the audit invariant "business record must not outlive the platform
    # row" holds and stale agents stop being dispatch-selectable.
    _soft_delete_queryset(
        Workload.objects.filter(
            registered_app_id=registered_app_id,
            deleted_at__isnull=True,
        ),
        "workloads",
    )

    # Load the app row once — needed both for the alert-rule ownership key
    # (org + slug) and for the final app-row soft-delete below.
    app = RegisteredApp.all_objects.get(pk=registered_app_id)

    # Alert rules. Seeded on registration (astrolift_operations.alert_seed)
    # with target="app" / target_id=<slug> — NOT a registered_app FK — so
    # they were invisible to this FK-driven soft-delete pass and survived
    # every deregister, leaving the /alerts page listing alerts for a deleted
    # app (reported for smd-fileportal). Soft-delete them by their (org, slug)
    # ownership key so they die with the app. Best-effort + idempotent.
    from astrolift_operations.alert_cleanup import soft_delete_app_alert_rules

    summary["alert_rules"] = soft_delete_app_alert_rules(
        organization_id=app.organization_id,
        app_slug=app.slug,
    )

    # Finally the app row itself.
    if app.deleted_at is None:
        app.deleted_at = now
        app.save(update_fields=["deleted_at", "updated_at", "version"])
        summary["registered_app"] = 1
    else:
        summary["registered_app"] = 0
    return summary


@activity.defn(name="astrolift.app.soft_delete_records")
async def soft_delete_app_records(
    registered_app_id: int,
) -> dict[str, int]:
    """Soft-delete the platform rows associated with the app.

    Per the soft-delete invariant in the policy module: business-
    record kinds (RegisteredApp, Deployment, AppEnvironment,
    AppSecretBundleRef, Workload, AlertRule) are soft-deleted; audit +
    event logs are retained.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    summary = await sync_to_async(_soft_delete_app_records_sync)(
        registered_app_id,
    )
    log.info(
        "soft_delete_app_records summary=%s",
        summary,
        extra={"registered_app_id": registered_app_id},
    )
    return summary
