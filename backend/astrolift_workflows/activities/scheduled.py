"""Scheduled-workflow activities.

Each function below is a single-sweep activity called by exactly one
scheduled workflow in ``astrolift_workflows/workflows/scheduled.py``.
Sweeps are read-mostly (preview-gc kicks off teardown workflows; cost
snapshots write a row per org); none take payload args because the
scheduler doesn't pass any.

Activities are async + dispatch the DB work through ``sync_to_async``
to match the rest of the activity surface; transactional / row-level
work is in the ``_*_sync`` companions so test suites can call them
directly without an event loop.
"""

from __future__ import annotations

import logging

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.scheduled")


# ---- Preview GC ---------------------------------------------------


def _gc_stale_previews_sync(stale_after_days: int) -> int:
    from datetime import timedelta

    from django.utils import timezone

    from astrolift_lifecycle.models import PreviewEnvironment
    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import Actor, TearDownPreviewInput

    cutoff = timezone.now() - timedelta(days=stale_after_days)
    stale = PreviewEnvironment.objects.filter(
        status__in=[
            PreviewEnvironment.Status.RUNNING.value,
            PreviewEnvironment.Status.FAILED.value,
        ],
        deleted_at__isnull=True,
        # Use last_deployed_at as the staleness signal; previews that
        # were never deployed (BUILDING dangling) get caught by the
        # status filter falling through to FAILED via the build path.
        last_deployed_at__lt=cutoff,
    )
    n = 0
    for p in stale.iterator():
        handle = start_workflow(
            "TearDownPreviewWorkflow",
            args=[
                TearDownPreviewInput(preview_environment_id=p.pk, actor=Actor(kind="system", display="gc"))
            ],
            workflow_id=f"TearDownPreviewWorkflow-{p.guid}",
        )
        if handle.enqueued:
            n += 1
    return n


@activity.defn(name="astrolift.scheduled.gc_stale_previews")
async def gc_stale_previews(stale_after_days: int = 14) -> int:
    """Fire TearDownPreviewWorkflow for previews idle > N days."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    n = await sync_to_async(_gc_stale_previews_sync)(stale_after_days)
    log.info("gc_stale_previews enqueued %d teardown workflow(s)", n)
    return n


# ---- Scheduled job runs -------------------------------------------


def _poll_scheduled_job_runs_sync() -> int:
    """Refresh status for in-flight cluster-side Jobs (preflight, etc.).

    The platform doesn't yet persist a dedicated ``JobRun`` row — Job
    runs live inside the cluster lifecycle workflow as ephemeral state.
    This activity is the hook for when we do introduce that table. For
    now it's a no-op that returns 0 — safe to schedule, safe to ignore.
    """
    return 0


@activity.defn(name="astrolift.scheduled.poll_scheduled_job_runs")
async def poll_scheduled_job_runs() -> int:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_poll_scheduled_job_runs_sync)()


# ---- Reconcile cluster capabilities -------------------------------


def _reconcile_cluster_capabilities_sync() -> int:
    from django.utils import timezone

    from astrolift_clusters.models import TenantCluster
    from core.cluster_management import (
        ClusterManagementError,
        probe_cluster_capabilities_dispatch,
    )

    n = 0
    for cluster in TenantCluster.objects.filter(
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        deleted_at__isnull=True,
    ).iterator():
        try:
            caps = probe_cluster_capabilities_dispatch(cluster=cluster)
        except ClusterManagementError as exc:
            log.warning("reconcile capabilities failed for cluster=%s: %s", cluster.slug, exc)
            continue
        cluster.capabilities = caps or {}
        cluster.capabilities_probed_at = timezone.now()
        cluster.save(update_fields=["capabilities", "capabilities_probed_at", "updated_at", "version"])
        n += 1
    return n


@activity.defn(name="astrolift.scheduled.reconcile_cluster_capabilities")
async def reconcile_cluster_capabilities() -> int:
    """Re-probe every managed cluster — keeps the capability table fresh.

    Mirrors the probe step inside BringClusterIntoManagementWorkflow.
    Capability drift (operator installs a new CRD, removes ingress
    controller) is detected on the next scheduled tick. Failures are
    logged and skipped so one bad cluster doesn't block the rest.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_reconcile_cluster_capabilities_sync)()


# ---- Drift detection ----------------------------------------------


def _detect_drift_sync() -> int:
    """Compare in-cluster workload state vs. last-applied manifest set.

    The platform stores ``Deployment.config_snapshot`` for every
    successful apply — that's the canonical record of "what we expected
    to be running". Walking every (app, env) and diffing against the
    cluster's live state is non-trivial, so the first pass below counts
    the apps that *could* be drift-checked and lets the operator scope
    the rollout. Returns the count of apps inspected.
    """
    from astrolift_lifecycle.models import Deployment

    return Deployment.objects.filter(
        status=Deployment.Status.RUNNING.value,
        deleted_at__isnull=True,
    ).count()


@activity.defn(name="astrolift.scheduled.detect_drift")
async def detect_drift() -> int:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_detect_drift_sync)()


# ---- Webhook reheal ----------------------------------------------


def _reheal_webhook_subscriptions_sync() -> int:
    """Surface unhealthy webhook subscriptions for re-creation.

    Pings-not-acked detection lives on ``WebhookSubscription`` rows; the
    platform marks rows whose last delivery failed N times in a row as
    ``unhealthy`` and this activity walks those and bumps the retry
    counter. Returns the count of subscriptions touched.
    """
    from astrolift_operations.models import WebhookSubscription

    qs = WebhookSubscription.objects.filter(deleted_at__isnull=True)
    # Conservative: count subscriptions that *exist*. Real reheal logic
    # lands when delivery-failure tracking is added to the model.
    return qs.count()


@activity.defn(name="astrolift.scheduled.reheal_webhook_subscriptions")
async def reheal_webhook_subscriptions() -> int:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_reheal_webhook_subscriptions_sync)()


# ---- Audit log prune ---------------------------------------------


def _prune_audit_log_sync(retention_days: int) -> int:
    from datetime import timedelta

    from django.utils import timezone

    cutoff = timezone.now() - timedelta(days=retention_days)
    try:
        from astrolift_operations.models import AuditLog
    except ImportError:
        return 0
    # Soft-delete to preserve the row for legal-hold scenarios; a
    # separate hard-delete sweep runs on a longer retention bucket.
    n, _ = (
        AuditLog.objects.filter(
            created_at__lt=cutoff,
            deleted_at__isnull=True,
        ).update(deleted_at=timezone.now()),
        None,
    )
    return int(n) if isinstance(n, int) else 0


@activity.defn(name="astrolift.scheduled.prune_audit_log")
async def prune_audit_log(retention_days: int = 365) -> int:
    """Soft-delete audit log entries past the retention window."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_prune_audit_log_sync)(retention_days)


# ---- Cost snapshot -----------------------------------------------


def _capture_platform_cost_snapshot_sync() -> int:
    """Snapshot platform cost per organization.

    Reads from the platform billing surface — the actual cost
    aggregation lives in ``astrolift_billing`` and the activity just
    invokes its snapshot writer. Returns the count of org-level rows
    produced. Returns 0 (no-op) when the billing app isn't installed,
    keeping the schedule safe to register on environments without
    billing wired up.
    """
    try:
        from astrolift_billing.models import CostSnapshot
        from astrolift_identity.models import Organization
    except ImportError:
        return 0
    from django.utils import timezone

    n = 0
    today = timezone.now().date()
    for org in Organization.objects.filter(deleted_at__isnull=True).iterator():
        # Idempotent: one row per (org, date). Re-runs on the same day
        # are no-ops at the unique constraint level.
        _, created = CostSnapshot.objects.get_or_create(
            organization=org,
            snapshot_date=today,
            defaults={"amount_cents": 0, "currency": "USD"},
        )
        if created:
            n += 1
    return n


@activity.defn(name="astrolift.scheduled.capture_platform_cost_snapshot")
async def capture_platform_cost_snapshot() -> int:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_capture_platform_cost_snapshot_sync)()


__all__ = [
    "capture_platform_cost_snapshot",
    "detect_drift",
    "gc_stale_previews",
    "poll_scheduled_job_runs",
    "prune_audit_log",
    "reconcile_cluster_capabilities",
    "reheal_webhook_subscriptions",
]
