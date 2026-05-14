"""Activities for the cluster bring-into-management workflow (#316).

Each activity is a thin wrapper around a single durable step:

  * ``verify_reachability``: read-only probe of the cluster's CRD
    catalog to confirm auth + network. Failure flips the row to
    ``error`` lifecycle before any RBAC write hits.
  * ``apply_platform_rbac``: idempotent server-side apply of the
    four-manifest platform RBAC bundle.
  * ``probe_capabilities``: capability snapshot the workflow
    persists onto ``TenantCluster.capabilities`` +
    ``capabilities_probed_at``.
  * ``run_preflight_job``: one-shot nginx-unprivileged Job in
    ``astrolift-system``. Passes when ``status.succeeded >= 1``
    within the timeout; fails otherwise.
  * ``mark_managed``: flip lifecycle to ``managed`` + set
    ``managed_at`` + clear ``last_management_error``.
  * ``mark_error``: flip lifecycle to ``error`` + persist the
    failure message.

Activities are split fine-grained so a single transient failure
(e.g. RBAC apply 503 from etcd) retries in isolation rather than
restarting the whole pipeline. The orchestrator workflow chains
them with explicit retry policies.
"""

from __future__ import annotations

import logging

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.cluster_management")


def _verify_reachability_sync(cluster_id: int) -> None:
    """Sync core — exposed so unit tests can call it directly."""
    from astrolift_clusters.models import TenantCluster
    from core.cluster_management import probe_cluster_capabilities_dispatch

    cluster = TenantCluster.all_objects.get(pk=cluster_id)
    # Even though the resolver gave us the row, the workflow could be
    # picked up by a delayed worker — re-read the live row state so a
    # mid-flight unregister surfaces here instead of after RBAC apply.
    if cluster.deleted_at is not None:
        raise RuntimeError(f"cluster {cluster.slug} was unregistered before management could run")
    if not cluster.is_active:
        raise RuntimeError(
            f"cluster {cluster.slug} is inactive — re-activate before bringing into management"
        )
    # A cheap read-only call: the probe lists CRDs. Auth / DNS / TLS
    # failure surfaces here as an exception with the cluster slug in
    # the message, which the workflow records in last_management_error.
    probe_cluster_capabilities_dispatch(cluster=cluster)


@activity.defn(name="astrolift.cluster.verify_reachability")
async def verify_reachability(cluster_id: int) -> None:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_verify_reachability_sync)(cluster_id)


def _apply_platform_rbac_sync(cluster_id: int) -> list[str]:
    from astrolift_clusters.models import TenantCluster
    from core.cluster_management import bring_cluster_into_management

    cluster = TenantCluster.all_objects.get(pk=cluster_id)
    # ``run_preflight=False`` because this activity owns only the RBAC
    # apply step. The orchestrator runs the preflight Job as a
    # separate activity so a Job-side failure doesn't roll back the
    # RBAC (it's idempotent + harmless to leave in place).
    report = bring_cluster_into_management(cluster=cluster, run_preflight=False)
    if not report.success:
        raise RuntimeError(report.error or "platform RBAC apply did not complete")
    return list(report.messages)


@activity.defn(name="astrolift.cluster.apply_platform_rbac")
async def apply_platform_rbac(cluster_id: int) -> list[str]:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_apply_platform_rbac_sync)(cluster_id)


def _probe_capabilities_sync(cluster_id: int) -> dict:
    from django.utils import timezone

    from astrolift_clusters.models import TenantCluster
    from core.cluster_management import probe_cluster_capabilities_dispatch

    cluster = TenantCluster.all_objects.get(pk=cluster_id)
    capabilities = probe_cluster_capabilities_dispatch(cluster=cluster)
    cluster.capabilities = capabilities
    cluster.capabilities_probed_at = timezone.now()
    cluster.save(update_fields=["capabilities", "capabilities_probed_at", "updated_at", "version"])
    return capabilities


@activity.defn(name="astrolift.cluster.probe_capabilities")
async def probe_capabilities(cluster_id: int) -> dict:
    """Run the capability probe + persist onto the row. Returns the
    capabilities dict so the workflow has it in the payload for
    downstream activities (none today, but the value lands in the
    Temporal history for debugging)."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_probe_capabilities_sync)(cluster_id)


def _run_preflight_job_sync(cluster_id: int) -> str:
    from astrolift_clusters.models import TenantCluster
    from core.cluster_management import bring_cluster_into_management

    cluster = TenantCluster.all_objects.get(pk=cluster_id)
    # Re-run with ``run_preflight=True`` — the orchestrator already
    # applied RBAC + probed in earlier activities; this re-runs both
    # (cheap, idempotent) plus runs the Job. We skip the probe-persist
    # step (probe_capabilities owns that) because the bring path's
    # capabilities are returned in the report rather than written.
    report = bring_cluster_into_management(cluster=cluster, run_preflight=True)
    if not report.success:
        raise RuntimeError(report.error or "preflight Job did not complete")
    return report.messages[-1] if report.messages else "preflight passed"


@activity.defn(name="astrolift.cluster.run_preflight_job")
async def run_preflight_job(cluster_id: int) -> str:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_run_preflight_job_sync)(cluster_id)


def _mark_managed_sync(cluster_id: int) -> None:
    from django.utils import timezone

    from astrolift_clusters.models import TenantCluster

    cluster = TenantCluster.all_objects.get(pk=cluster_id)
    cluster.lifecycle = TenantCluster.Lifecycle.MANAGED.value
    cluster.managed_at = timezone.now()
    cluster.last_management_error = ""
    cluster.save(
        update_fields=[
            "lifecycle",
            "managed_at",
            "last_management_error",
            "updated_at",
            "version",
        ]
    )


@activity.defn(name="astrolift.cluster.mark_managed")
async def mark_managed(cluster_id: int) -> None:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_managed_sync)(cluster_id)


def _mark_error_sync(cluster_id: int, message: str) -> None:
    from astrolift_clusters.models import TenantCluster

    cluster = TenantCluster.all_objects.get(pk=cluster_id)
    cluster.lifecycle = TenantCluster.Lifecycle.ERROR.value
    # Cap the persisted message so a 50-line traceback doesn't blow
    # up the TextField — workflow callers truncate too, but defend
    # in depth so a malformed activity error doesn't fail to save.
    cluster.last_management_error = (message or "unknown error")[:4000]
    cluster.save(
        update_fields=[
            "lifecycle",
            "last_management_error",
            "updated_at",
            "version",
        ]
    )


@activity.defn(name="astrolift.cluster.mark_error")
async def mark_error(cluster_id: int, message: str) -> None:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_error_sync)(cluster_id, message)


def _mark_managing_sync(cluster_id: int) -> None:
    from astrolift_clusters.models import TenantCluster

    cluster = TenantCluster.all_objects.get(pk=cluster_id)
    # Idempotent — re-running on a managed cluster (refresh) flips it
    # back to managing for the duration of the workflow + back to
    # managed at the end. The UI polls and renders the spinner.
    if cluster.lifecycle == TenantCluster.Lifecycle.MANAGING.value:
        return
    cluster.lifecycle = TenantCluster.Lifecycle.MANAGING.value
    cluster.last_management_error = ""
    cluster.save(
        update_fields=[
            "lifecycle",
            "last_management_error",
            "updated_at",
            "version",
        ]
    )


@activity.defn(name="astrolift.cluster.mark_managing")
async def mark_managing(cluster_id: int) -> None:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_managing_sync)(cluster_id)
