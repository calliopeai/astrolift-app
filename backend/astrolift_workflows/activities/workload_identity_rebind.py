"""Move a re-pointed cluster's per-app role trust to its new OIDC issuer (#2472).

``register_tenant_cluster`` records a ``ClusterWorkloadIdentityRebind`` when
an upsert points a cluster row at a cluster with another issuer. This walks
every live app with a live environment on the cluster, or bound to it by
default, and moves its runtime role's trust (``workload_identity_role_name``)
from the row's previous issuers to the new one. A role that trusts another
cluster is left alone. Progress and per-app failures are written to the
request as it goes, which is what the cluster page reads.

Build roles are not touched: every build re-mints its own role against the
cluster's current issuer before it runs.
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from astrolift_workflows.activity_heartbeat import heartbeat_while

log = logging.getLogger("astrolift_workflows.activities.workload_identity_rebind")

REBIND_INCOMPLETE = "WorkloadIdentityRebindIncomplete"


def _apps_on_cluster(cluster: Any) -> list[Any]:
    from django.db.models import Q

    from astrolift_registry.models import RegisteredApp

    apps = RegisteredApp.objects.filter(
        Q(environments__tenant_cluster=cluster, environments__deleted_at__isnull=True)
        | Q(default_tenant_cluster=cluster),
        deleted_at__isnull=True,
        organization__deleted_at__isnull=True,
    )
    if cluster.organization_id is not None:
        apps = apps.filter(organization_id=cluster.organization_id)
    return list(apps.select_related("organization").distinct().order_by("pk"))


def _superseded(rebind: Any) -> bool:
    from astrolift_clusters.models import ClusterWorkloadIdentityRebind

    return ClusterWorkloadIdentityRebind.objects.filter(
        tenant_cluster_id=rebind.tenant_cluster_id, pk__gt=rebind.pk
    ).exists()


def _fail(rebind: Any, error: str) -> None:
    from django.utils import timezone

    from astrolift_clusters.models import ClusterWorkloadIdentityRebind

    rebind.state = ClusterWorkloadIdentityRebind.State.FAILED
    rebind.error = error[:2000]
    rebind.finished_at = timezone.now()
    rebind.save(update_fields=["state", "error", "finished_at", "updated_at", "version"])


def _rebind_cluster_sync(rebind_id: int) -> dict[str, Any]:
    from django.utils import timezone

    from astrolift_clusters.models import ClusterWorkloadIdentityRebind, TenantCluster
    from astrolift_clusters.workload_identity_rebind import normalize_issuer
    from astrolift_workflows.activities.workload_identity import _ensure_cluster_oidc_issuer
    from core.app_deploy import _config_for_capability, driver_for_capability, workload_identity_role_name

    rebind = ClusterWorkloadIdentityRebind.objects.get(pk=rebind_id)
    summary: dict[str, Any] = {"rebind": str(rebind.guid), "issuer": rebind.issuer}
    if rebind.state == ClusterWorkloadIdentityRebind.State.SUCCEEDED:
        return {**summary, "state": rebind.state}
    if _superseded(rebind):
        return {**summary, "superseded": True}

    cluster = TenantCluster.all_objects.select_related("provider_plugin").get(pk=rebind.tenant_cluster_id)
    try:
        if cluster.deleted_at is not None or not cluster.is_active:
            raise ValueError("the cluster was unregistered or deactivated before its roles moved")
        if cluster.provider_plugin.slug != "aws":
            raise ValueError(f"the cluster's provider is {cluster.provider_plugin.slug!r}, not aws")
        # A registration without discovery replaces auth_config with nothing in
        # it; EKS still knows the issuer.
        _ensure_cluster_oidc_issuer(cluster)
        issuer = normalize_issuer(_config_for_capability("aws", cluster, "identity").cluster_oidc_issuer)
        if issuer != rebind.issuer:
            raise ValueError(
                f"the cluster's OIDC issuer is {issuer or 'unknown'}, not {rebind.issuer}; "
                "register the cluster again to move its roles"
            )
        driver = driver_for_capability(cluster, "identity")
        apps = _apps_on_cluster(cluster)
    except Exception as exc:
        _fail(rebind, str(exc))
        raise ApplicationError(str(exc), type=REBIND_INCOMPLETE, non_retryable=True) from exc

    rebind.state = ClusterWorkloadIdentityRebind.State.RUNNING
    rebind.started_at = timezone.now()
    rebind.finished_at = None
    rebind.apps_total = len(apps)
    rebind.apps_reconciled = 0
    rebind.failures = []
    rebind.error = ""
    rebind.save(
        update_fields=[
            "state",
            "started_at",
            "finished_at",
            "apps_total",
            "apps_reconciled",
            "failures",
            "error",
            "updated_at",
            "version",
        ]
    )

    outcomes: dict[str, int] = {}
    for app in apps:
        # A newer re-point owns the roles from here; it carries this one's
        # issuers forward, so whatever this run did not reach it will.
        if _superseded(rebind):
            return {**summary, "superseded": True, "outcomes": outcomes}
        role = workload_identity_role_name(app)
        owner = {
            "organization": str(app.organization.guid),
            "app": str(app.guid),
            "cluster": str(cluster.guid),
        }
        try:
            outcome = driver.rebind_trust_issuer(
                role, previous_issuers=list(rebind.previous_issuers), owner=owner
            )
        except Exception as exc:  # noqa: BLE001 - one app's role must not strand the others
            rebind.failures = [
                *rebind.failures,
                {"app": f"{app.organization.slug}/{app.slug}", "role": role, "error": str(exc)[:500]},
            ]
        else:
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            rebind.apps_reconciled += 1
        rebind.save(update_fields=["apps_reconciled", "failures", "updated_at", "version"])

    rebind.state = (
        ClusterWorkloadIdentityRebind.State.FAILED
        if rebind.failures
        else ClusterWorkloadIdentityRebind.State.SUCCEEDED
    )
    rebind.finished_at = timezone.now()
    rebind.save(update_fields=["state", "finished_at", "updated_at", "version"])
    summary.update(
        state=rebind.state,
        apps_total=rebind.apps_total,
        apps_reconciled=rebind.apps_reconciled,
        failures=len(rebind.failures),
        outcomes=outcomes,
    )
    if rebind.failures:
        # Failed rather than retried here: a per-app failure is a refusal or a
        # permission, not a blip (botocore retries those). The next start of
        # the request, on the next registration, runs it again.
        raise ApplicationError(
            f"{len(rebind.failures)} of {rebind.apps_total} app roles were not moved to {rebind.issuer}",
            summary,
            type=REBIND_INCOMPLETE,
            non_retryable=True,
        )
    return summary


def _mark_failed_sync(rebind_id: int, error: str) -> bool:
    from astrolift_clusters.models import ClusterWorkloadIdentityRebind

    rebind = ClusterWorkloadIdentityRebind.objects.filter(pk=rebind_id).first()
    unfinished = (ClusterWorkloadIdentityRebind.State.PENDING, ClusterWorkloadIdentityRebind.State.RUNNING)
    # A walk that failed recorded its own reason and per-app failures.
    if rebind is None or rebind.state not in unfinished:
        return False
    _fail(rebind, error)
    return True


@activity.defn(name="astrolift.workload_identity.rebind_cluster_failed")
async def mark_cluster_workload_identity_rebind_failed(rebind_id: int, error: str) -> bool:
    """Activity entry: fail a request whose run ended before the walk recorded
    an outcome (its attempts ran out, or it timed out), which the cluster page
    would otherwise show as a move in flight with nothing running it."""
    from asgiref.sync import sync_to_async

    return await sync_to_async(_mark_failed_sync)(rebind_id, error)


@activity.defn(name="astrolift.workload_identity.rebind_cluster")
async def rebind_cluster_workload_identity(rebind_id: int) -> dict[str, Any]:
    """Activity entry: move the request's per-app role trust to its issuer."""
    from asgiref.sync import sync_to_async

    summary = await heartbeat_while(sync_to_async(_rebind_cluster_sync)(rebind_id))
    log.info("rebind_cluster_workload_identity rebind=%s summary=%s", rebind_id, summary)
    return summary
