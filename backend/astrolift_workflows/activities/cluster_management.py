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
from typing import Any

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.cluster_management")


def _verify_reachability_sync(cluster_id: int) -> None:
    """Sync core — exposed so unit tests can call it directly."""
    from astrolift_clusters.models import TenantCluster
    from core.cluster_credentials import record_verified_account
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
    # Before the probe: prove which account this cluster is in and save the
    # answer (#1422). A cluster naming an account its credential is not in
    # would otherwise pass every check here and every check after, then
    # provision into the wrong account while handing back ARNs naming the
    # declared one. Recorded rather than merely checked, because a cluster
    # cannot move between accounts — the saved value is what ARNs are built
    # from afterwards, and it is the one that was verified.
    record_verified_account(cluster)
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


def _provision_secrets_backend_sync(cluster_id: int) -> dict[str, Any]:
    """Idempotent — re-runs are safe.

    Resolves the cluster's SecretsBackend via ``driver_for_capability``
    and calls ``ensure_initialized()`` which performs the one-time
    bootstrap (CSI driver install / KMS key creation / Vault auth
    setup, depending on driver). Backends that don't need init raise
    ``NotImplementedError`` from the default protocol stub — we catch
    that and return ``skipped=True`` so the workflow proceeds without
    failure.
    """
    from django.utils import timezone

    from astrolift_clusters.models import TenantCluster
    from core.app_deploy import AppDeployError, driver_for_capability

    cluster = TenantCluster.all_objects.get(pk=cluster_id)
    try:
        driver = driver_for_capability(cluster, "secrets")
    except AppDeployError as exc:
        # Plugin doesn't register a secrets driver at all — surface as a
        # skipped step so the workflow continues; the operator can wire
        # one in later without re-running cluster bring.
        return {
            "ok": True,
            "skipped": True,
            "reason": f"no secrets driver registered for cluster {cluster.slug}: {exc}",
        }

    try:
        result = driver.ensure_initialized()
    except NotImplementedError as exc:
        # Default protocol behavior — backend needs no out-of-band
        # bootstrap (e.g. AWS Secrets Manager with cluster-default KMS).
        return {
            "ok": True,
            "skipped": True,
            "reason": f"backend does not require initialization: {exc}",
        }

    cluster.secrets_backend_provisioned_at = timezone.now()
    cluster.save(
        update_fields=[
            "secrets_backend_provisioned_at",
            "updated_at",
            "version",
        ]
    )
    payload: dict[str, Any] = {"ok": True, "skipped": False}
    if isinstance(result, dict):
        payload["result"] = result
    return payload


@activity.defn(name="astrolift.cluster.provision_secrets_backend")
async def provision_secrets_backend(cluster_id: int) -> dict[str, Any]:
    """Idempotent — re-runs are safe. Calls
    SecretsBackend.ensure_initialized()."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_provision_secrets_backend_sync)(cluster_id)


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


# ---- Cluster decommission ----------------------------------------------
#
# Inverse of bring-into-management: lifts the platform RBAC, severs app
# bindings, and flips the row to ``decommissioned``. Designed to be
# triggered manually by an operator (the removeClusterFromManagement
# mutation) when the cluster is being torn down out-of-band, OR by an
# admin reclaim flow when an organization is being offboarded.


def _ensure_cluster_drained_sync(cluster_id: int) -> int:
    """Refuse to decommission a cluster that still has bound app envs.

    Returns 0 when the cluster is drained. Raises with a clear message
    listing the count of bound envs otherwise. ``AppEnvironment.tenant_cluster``
    is a non-nullable PROTECT FK — operators must migrate the envs to a
    new cluster (via ``MigrateAppWorkflow``) or delete them before
    decommission can proceed.
    """
    from astrolift_clusters.models import TenantCluster
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_services.cluster_retirement import MODEL_CLEANUP_REQUIRED, has_cluster_owned_models
    from core.cluster_management import ClusterManagementError

    cluster = TenantCluster.all_objects.get(pk=cluster_id)
    if has_cluster_owned_models(cluster.pk):
        raise ClusterManagementError(MODEL_CLEANUP_REQUIRED)
    bound_count = AppEnvironment.objects.filter(
        tenant_cluster=cluster,
        deleted_at__isnull=True,
    ).count()
    if bound_count > 0:
        raise ClusterManagementError(
            f"cluster {cluster.slug!r} has {bound_count} active app environment(s) bound — "
            "migrate them to a different cluster or delete them first",
        )
    return 0


@activity.defn(name="astrolift.cluster.ensure_drained")
async def ensure_cluster_drained(cluster_id: int) -> int:
    """Refuse decommission when app envs are bound to this cluster."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_ensure_cluster_drained_sync)(cluster_id)


def _mark_decommissioning_sync(cluster_id: int) -> None:
    from django.db import transaction

    from astrolift_clusters.models import TenantCluster

    with transaction.atomic():
        cluster = TenantCluster.all_objects.select_for_update().get(pk=cluster_id)
        _ensure_cluster_drained_sync(cluster_id)
        if cluster.lifecycle == TenantCluster.Lifecycle.DECOMMISSIONING.value:
            return
        cluster.lifecycle = TenantCluster.Lifecycle.DECOMMISSIONING.value
        cluster.last_management_error = ""
        cluster.save(update_fields=["lifecycle", "last_management_error", "updated_at", "version"])


@activity.defn(name="astrolift.cluster.mark_decommissioning")
async def mark_decommissioning(cluster_id: int) -> None:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_decommissioning_sync)(cluster_id)


def _remove_platform_rbac_sync(cluster_id: int) -> str:
    """Delete the ``astrolift-system`` namespace, which cascades the
    platform RBAC bundle (ServiceAccount + ClusterRole +
    ClusterRoleBinding + the Job machinery used at preflight time).

    Idempotent — ``delete_namespace`` is a no-op when the namespace is
    already gone, so a re-run on a half-decommissioned cluster reaches
    a clean terminal state. Failures bubble up; the workflow flips to
    ERROR and the operator can retry.
    """
    from astrolift_clusters.models import TenantCluster
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    cluster = TenantCluster.all_objects.get(pk=cluster_id)
    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    # delete_namespace(wait=True) blocks until k8s reports the namespace
    # gone — picking that over wait=False so the workflow's terminal
    # state reflects a clean cluster rather than a Terminating phase.
    driver.delete_namespace(ctx.slug, "astrolift-system", wait=True)
    return "astrolift-system"


@activity.defn(name="astrolift.cluster.remove_platform_rbac")
async def remove_platform_rbac(cluster_id: int) -> str:
    """Lift the platform RBAC bundle — inverse of apply_platform_rbac."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_remove_platform_rbac_sync)(cluster_id)


def _mark_decommissioned_sync(cluster_id: int) -> None:
    from astrolift_clusters.models import TenantCluster

    cluster = TenantCluster.all_objects.get(pk=cluster_id)
    cluster.lifecycle = TenantCluster.Lifecycle.DECOMMISSIONED.value
    cluster.last_management_error = ""
    # managed_at is preserved as a historical marker — when this cluster
    # was last in the deploy pool. Operators can audit the lifecycle
    # arc without consulting the workflow_run log.
    cluster.save(update_fields=["lifecycle", "last_management_error", "updated_at", "version"])


@activity.defn(name="astrolift.cluster.mark_decommissioned")
async def mark_decommissioned(cluster_id: int) -> None:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_decommissioned_sync)(cluster_id)


def _teardown_cluster_infra_sync(cluster_id: int, delete_cloud_infra: bool) -> dict[str, Any]:
    """Delete the cluster's cloud infrastructure (EKS / GKE / AKS) via
    the provider driver's ``teardown_cluster``. Returns a serializable
    dict view of the ``TeardownReport`` so the workflow event log
    captures what was deleted.

    The driver decides what "delete" means per cloud:
      - aws: drain node groups → delete Fargate profiles → delete cluster
      - gcp: container.delete_cluster (cascades node pools)
      - azure: managed_clusters.begin_delete (cascades MC resource group)
      - k8s_native: no-op (bare metal is operator-owned)

    Idempotent per driver. The activity itself is idempotent at the DB
    layer too — re-running on a decommissioned row that's already had
    its cloud infra deleted just gets a report of "all already gone".
    """
    from astrolift_clusters.models import TenantCluster
    from core.cluster_management import ClusterManagementError, teardown_cluster_dispatch

    cluster = TenantCluster.all_objects.get(pk=cluster_id)
    try:
        report = teardown_cluster_dispatch(cluster=cluster, delete_cloud_infra=delete_cloud_infra)
    except ClusterManagementError as exc:
        raise RuntimeError(str(exc)) from exc
    if not report.success:
        # Driver returned structured failure — raise so Temporal retries
        # per the activity's RetryPolicy and the workflow can flip to
        # error if all retries exhaust.
        raise RuntimeError(report.error or "teardown_cluster reported failure")
    return {
        "success": True,
        "deleted": list(report.deleted),
        "skipped": list(report.skipped),
        "messages": list(report.messages),
    }


@activity.defn(name="astrolift.cluster.teardown_cluster_infra")
async def teardown_cluster_infra(
    cluster_id: int,
    delete_cloud_infra: bool = False,
) -> dict[str, Any]:
    """Delete the cluster's cloud infrastructure. Gated on the operator
    explicitly opting in via ``delete_cloud_infra=True``; default is a
    no-op for symmetry with the protocol (the historical decommission
    behavior leaves the cluster running, just removes platform RBAC)."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_teardown_cluster_infra_sync)(cluster_id, delete_cloud_infra)
