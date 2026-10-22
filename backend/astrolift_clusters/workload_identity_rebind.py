"""Move per-app role trust when a cluster row is re-pointed (#2472).

``register_tenant_cluster`` upserts a cluster row by slug, and the installer's
region change relies on that: the target region's cluster registers under the
old region's slug, so the row the restored database holds now names the new
cluster. Every per-app role minted for the row still trusts the old cluster's
OIDC provider, so pods on the new cluster cannot assume it until the app is
deployed again. When the upsert changes the row's issuer this records a
``ClusterWorkloadIdentityRebind`` and starts the workflow that moves the
roles' trust, recording its progress and failures on that row.

IRSA only: GKE Workload Identity binds no cluster issuer, and AKS federated
credentials are written by an identity path the deploy reconciles.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def normalize_issuer(value: Any) -> str:
    """An OIDC issuer as a trust policy names it: host and path, no scheme."""
    return str(value or "").strip().removeprefix("https://").rstrip("/")


def cluster_issuers(cluster: Any) -> set[str]:
    """Every issuer the row records: the one the IRSA driver reads from
    ``auth_config`` and the one discovery writes to ``provider_config``."""
    values = (
        (cluster.auth_config or {}).get("cluster_oidc_issuer"),
        (cluster.provider_config or {}).get("cluster_oidc_issuer"),
    )
    return {issuer for issuer in map(normalize_issuer, values) if issuer}


def current_issuer(cluster: Any) -> str:
    """The issuer the row's roles should trust now.

    ``auth_config`` is what the IRSA driver reads. A registration without
    discovery replaces ``auth_config`` with nothing in it, while the
    discovered value survives in the merged ``provider_config``.
    """
    return normalize_issuer((cluster.auth_config or {}).get("cluster_oidc_issuer")) or normalize_issuer(
        (cluster.provider_config or {}).get("cluster_oidc_issuer")
    )


def request_workload_identity_rebind(cluster: Any, previous_issuers: set[str]) -> Any | None:
    """Record and start the rebind a re-point needs, or start an unfinished one again.

    ``previous_issuers`` are the row's issuers before the upsert. A request
    still pending, running or failed for the current issuer is started again,
    which joins a running workflow and retries a failed one, so a control
    plane that could not reach Temporal when the row moved catches up on its
    next start. Returns the request started, or None.
    """
    from astrolift_clusters.models import ClusterWorkloadIdentityRebind

    if getattr(getattr(cluster, "provider_plugin", None), "slug", "") != "aws":
        return None
    issuer = current_issuer(cluster)
    if not issuer:
        return None
    latest = ClusterWorkloadIdentityRebind.objects.filter(tenant_cluster=cluster).order_by("-pk").first()
    unfinished = latest is not None and latest.state != ClusterWorkloadIdentityRebind.State.SUCCEEDED
    moved_from = {value for value in previous_issuers if value} - {issuer}
    if moved_from:
        if unfinished:
            # Roles that request had not reached yet still trust what it was
            # moving them from, or already trust its target.
            moved_from |= {*latest.previous_issuers, latest.issuer}
            moved_from.discard(issuer)
        rebind = ClusterWorkloadIdentityRebind.objects.create(
            tenant_cluster=cluster,
            issuer=issuer,
            previous_issuers=sorted(moved_from),
        )
    elif unfinished and latest.issuer == issuer:
        rebind = latest
    else:
        return None
    start_workload_identity_rebind(rebind)
    return rebind


def start_workload_identity_rebind(rebind: Any) -> bool:
    """Start the request's workflow; never raises.

    The id is the request's own, started without replacing a running one, so
    starting it again joins the run in flight and retries one that failed.
    A failed start is recorded on the request, which stays pending.
    """
    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import Actor, RebindClusterWorkloadIdentityInput

    try:
        start_workflow(
            "RebindClusterWorkloadIdentityWorkflow",
            args=[
                RebindClusterWorkloadIdentityInput(
                    rebind_id=rebind.pk,
                    actor=Actor(kind="system", display="cluster-repoint"),
                )
            ],
            workflow_id=rebind.workflow_id,
            replace_running=False,
        )
    except Exception as exc:  # noqa: BLE001 - runs on every control-plane start; Temporal may be down
        logger.exception("workload identity rebind %s could not be started", rebind.guid)
        rebind.error = f"could not start the rebind: {exc}"[:2000]
        rebind.save(update_fields=["error", "updated_at", "version"])
        return False
    logger.info("workload identity rebind %s started for issuer %s", rebind.guid, rebind.issuer)
    return True
