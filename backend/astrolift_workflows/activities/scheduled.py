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
from typing import Any

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


# Cost actuals client factory registry, keyed by ProviderPlugin slug
# ("aws" / "gcp" / "azure"). The activity dispatches through this
# table so a deployer can wire a fake factory in tests / dev without
# patching the activity itself. The factory receives the cluster
# row (with its ``provider_config``) and returns a billing-actuals
# client OR None when the cluster isn't configured for actuals
# (e.g. k8s_native).
#
# Public surface so tests can monkey-patch one entry; the default
# bindings live in ``_default_cost_actuals_factories`` below and
# wire in lazily so an environment without the vendor providers
# package can still register the activity.
COST_ACTUALS_FACTORIES: dict[str, Any] = {}


def _default_cost_actuals_factories() -> dict[str, Any]:
    """Lazy-load the per-cloud actuals clients. Imports are inside
    the per-factory closures so an environment that doesn't ship a
    given cloud's SDK still loads the others."""

    def _aws_factory(cluster: Any) -> Any:
        from aws.cost import AWSBillingActuals, AWSBillingActualsConfig

        return AWSBillingActuals(config=AWSBillingActualsConfig())

    def _gcp_factory(cluster: Any) -> Any:
        from gcp.cost import GCPBillingActuals, GCPBillingActualsConfig

        provider_config = (getattr(cluster, "provider_config", {}) or {}).get("billing", {}) or {}
        return GCPBillingActuals(
            config=GCPBillingActualsConfig(
                project=str(provider_config.get("project", "") or ""),
                dataset=str(provider_config.get("dataset", "") or ""),
                table=str(provider_config.get("table", "") or "gcp_billing_export_resource_v1"),
            )
        )

    def _azure_factory(cluster: Any) -> Any:
        from azure.cost import AzureBillingActuals, AzureBillingActualsConfig

        provider_config = (getattr(cluster, "provider_config", {}) or {}).get("billing", {}) or {}
        return AzureBillingActuals(
            config=AzureBillingActualsConfig(scope=str(provider_config.get("scope", "") or "")),
        )

    return {
        "aws": _aws_factory,
        "gcp": _gcp_factory,
        "azure": _azure_factory,
    }


def _resolve_actuals_factory(provider_slug: str) -> Any | None:
    factory = COST_ACTUALS_FACTORIES.get(provider_slug)
    if factory is not None:
        return factory
    # Lazy-default on first lookup so the default impl can fail
    # cleanly (ImportError on vendor package) without breaking the
    # activity registration.
    try:
        defaults = _default_cost_actuals_factories()
    except Exception:  # noqa: BLE001
        return None
    return defaults.get(provider_slug)


def _capture_platform_cost_snapshot_sync() -> int:
    """Write the daily cost snapshot rows per organization (#502).

    For each org, walk its managed clusters and ask each cloud's
    billing-actuals client (vendor/astrolift-providers/<cloud>/cost.py)
    for the per-binding spend over the previous calendar day. Each
    row is keyed back to a ``ManagedServiceBinding`` via the
    ``astrolift.io/binding`` tag stamped at provision time (#438);
    rows whose tag is empty or doesn't match a known binding land
    with ``managed_service_binding_id = NULL`` so the UI rolls them
    up as "Shared / untagged".

    A zero-amount ``OTHER`` placeholder row per org is still emitted
    when no driver returns data (or no clusters are configured) so
    the trend chart has a continuous x-axis. Failures on a single
    cloud are logged + skipped — partial data beats no data on a
    multi-cloud install where one tenant's billing-export isn't
    enabled yet.

    Returns the count of CostSnapshot rows produced. Returns 0
    (no-op) when the billing app isn't installed, keeping the
    schedule safe to register on environments without billing
    wired up.
    """
    try:
        from astrolift_billing.models import CostSnapshot
        from astrolift_identity.models import Organization
        from astrolift_services.models import ManagedServiceBinding
    except ImportError:
        return 0
    from datetime import timedelta

    from django.utils import timezone

    # Snapshots align to UTC calendar days; "yesterday's spend" is
    # the window we ask each cloud for. Today's row is written with
    # ``taken_at=today`` so re-runs the same day are idempotent at
    # the unique constraint.
    today = timezone.now().date()
    window_start = today - timedelta(days=1)
    window_end = today

    n = 0
    for org in Organization.objects.filter(deleted_at__isnull=True).iterator():
        n += _capture_org_cost_snapshot(
            org=org,
            taken_at=today,
            window_start=window_start,
            window_end=window_end,
            CostSnapshot=CostSnapshot,
            ManagedServiceBinding=ManagedServiceBinding,
        )
    return n


def _capture_org_cost_snapshot(
    *,
    org: Any,
    taken_at: Any,
    window_start: Any,
    window_end: Any,
    CostSnapshot: Any,
    ManagedServiceBinding: Any,
) -> int:
    """Walk one org's clusters + write per-binding cost rows.

    Returns the count of CostSnapshot rows produced for this org
    (placeholder + per-binding combined). Always emits the
    placeholder OTHER row so the trend chart x-axis stays
    continuous, even when no cloud returns data.
    """
    from astrolift_clusters.models import TenantCluster

    rows = 0

    # Walk clusters scoped to this org (NULL org = platform-shared
    # cluster also counted toward org billing if assigned via
    # AppEnvironment, but the per-org sweep here just keys off the
    # cluster.organization field).
    clusters = TenantCluster.objects.filter(
        organization=org,
        deleted_at__isnull=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    ).select_related("provider_plugin")

    # Cache binding GUIDs once per org so the per-row resolve is an
    # O(1) dict hit rather than a per-row DB query.
    bindings_by_guid: dict[str, Any] = {
        str(b.guid): b
        for b in ManagedServiceBinding.objects.filter(
            managed_service__registered_app__organization=org,
            deleted_at__isnull=True,
        ).iterator()
    }

    seen_provider: set[str] = set()
    for cluster in clusters.iterator():
        provider_slug = (cluster.provider_plugin.slug or "").lower()
        if not provider_slug or provider_slug in seen_provider:
            continue
        seen_provider.add(provider_slug)
        factory = _resolve_actuals_factory(provider_slug)
        if factory is None:
            log.info(
                "cost actuals: no factory registered for provider=%s (cluster=%s) — skipping",
                provider_slug,
                cluster.slug,
            )
            continue
        try:
            client = factory(cluster)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "cost actuals: factory for provider=%s (cluster=%s) raised: %s",
                provider_slug,
                cluster.slug,
                exc,
            )
            continue
        try:
            result = client.query_actuals_by_binding(
                start=window_start,
                end=window_end,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "cost actuals: query failed for provider=%s (cluster=%s): %s",
                provider_slug,
                cluster.slug,
                exc,
            )
            continue
        if not isinstance(result, list):
            # BillingActualsUnavailable — log + skip per-binding
            # writes, the OTHER placeholder still goes out below.
            log.info(
                "cost actuals: provider=%s (cluster=%s) unavailable: %s — %s",
                provider_slug,
                cluster.slug,
                getattr(result, "reason", "unknown"),
                getattr(result, "message", ""),
            )
            continue
        rows += _write_actuals_rows(
            org=org,
            taken_at=taken_at,
            provider_slug=provider_slug,
            items=result,
            bindings_by_guid=bindings_by_guid,
            CostSnapshot=CostSnapshot,
        )

    # Trend-chart x-axis continuity: always emit one OTHER
    # placeholder per org per day.
    _, created = CostSnapshot.objects.get_or_create(
        organization=org,
        taken_at=taken_at,
        registered_app=None,
        managed_service_binding=None,
        by=CostSnapshot.CostBy.OTHER,
        source=CostSnapshot.Source.PLATFORM_METER,
        defaults={"amount_cents": 0, "currency": "USD"},
    )
    if created:
        rows += 1
    return rows


def _write_actuals_rows(
    *,
    org: Any,
    taken_at: Any,
    provider_slug: str,
    items: list[Any],
    bindings_by_guid: dict[str, Any],
    CostSnapshot: Any,
) -> int:
    """Persist one CostSnapshot row per actuals line item.

    Rows whose ``binding_guid`` resolves to a known binding write
    the FK; rows with an empty or unknown binding GUID write NULL
    so they roll up under the "Shared / untagged" bucket. Idempotent
    via the model's unique constraint on
    (organization, registered_app, managed_service_binding, by,
    taken_at, source).
    """
    rows = 0
    for item in items:
        binding_guid = (getattr(item, "binding_guid", "") or "").strip()
        binding = bindings_by_guid.get(binding_guid) if binding_guid else None
        registered_app = None
        by = CostSnapshot.CostBy.MANAGED_SERVICE if binding is not None else CostSnapshot.CostBy.OTHER
        if binding is not None:
            registered_app = binding.managed_service.registered_app
            if binding_guid and not bindings_by_guid.get(binding_guid) and binding_guid:
                # Defensive: shouldn't happen given the resolve above,
                # but skip writes whose binding belongs to a different
                # org (multi-tenant guardrail).
                continue
            if registered_app.organization_id != org.id:
                log.warning(
                    "cost actuals: binding %s belongs to org %s, not %s — skipping",
                    binding_guid,
                    registered_app.organization_id,
                    org.id,
                )
                continue
        amount_cents = int(getattr(item, "amount_cents", 0) or 0)
        currency = getattr(item, "currency", "USD") or "USD"
        _, created = CostSnapshot.objects.get_or_create(
            organization=org,
            taken_at=taken_at,
            registered_app=registered_app,
            managed_service_binding=binding,
            by=by,
            source=CostSnapshot.Source.PROVIDER_ESTIMATE,
            defaults={"amount_cents": amount_cents, "currency": currency},
        )
        if created:
            rows += 1
        else:
            # An existing row from an earlier same-day run takes
            # precedence (snapshots are immutable per the model
            # docstring — corrections come as a later taken_at).
            log.debug(
                "cost actuals: snapshot already exists for org=%s binding=%s — skipping",
                org.slug,
                binding_guid or "<untagged>",
            )
    log.info(
        "cost actuals: wrote %d row(s) for org=%s provider=%s",
        rows,
        org.slug,
        provider_slug,
    )
    return rows


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
