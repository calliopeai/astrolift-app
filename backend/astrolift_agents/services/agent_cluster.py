"""Shared cluster resolution for the agent dispatch path (#1173).

Agent tasks — and the secret store the platform writes their values into —
must resolve to the *same* cluster the dispatcher spawns onto, so a value
set via ``setAgentSecretValue`` lands in the exact store the pod reads at
launch. This is that single resolution point: the spawner
(``_spawn_agent_task_sync`` -> ``K8sJobSpawner``), the secret-value
mutations, and the secret-status query all route through it.

Agent stages are org-scoped (not app-scoped), so they run on the org's
default managed cluster: an org-owned :class:`TenantCluster` when one
exists, otherwise a shared platform cluster the install registered with a
NULL organization (the same EKS the org's apps deploy onto). Only
``managed`` rows are deploy targets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrolift_clusters.models import TenantCluster


class NoAgentClusterError(RuntimeError):
    """Raised when an organization has no managed cluster to dispatch onto."""


def resolve_agent_cluster(organization) -> TenantCluster:
    """Return the org's managed cluster for agent dispatch, or raise.

    Prefers an org-owned managed cluster, falling back to a shared
    platform cluster (``organization`` NULL). ``registered`` /
    ``error`` / ``decommissioned`` rows are skipped. Raises
    :class:`NoAgentClusterError` when nothing qualifies.
    """
    from django.db.models import F, Q

    from astrolift_clusters.models import TenantCluster

    cluster = (
        TenantCluster.objects.filter(
            Q(organization=organization) | Q(organization__isnull=True),
            deleted_at__isnull=True,
            lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        )
        .order_by(F("organization_id").asc(nulls_last=True), "created_at")
        .first()
    )
    if cluster is None:
        raise NoAgentClusterError(
            f"organization {organization.slug!r} has no managed cluster — "
            f"agent dispatch/secret resolution cannot proceed"
        )
    return cluster
