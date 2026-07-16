"""Dispatch router — selects a cluster for a pipeline job based on runs_on labels.

Decision logic (from issue #82):

1. Parse the job's ``runs_on`` value into a normalized label set.
2. Apply special-label rules:
   - ``self-hosted`` → runner-only; never K8s (not implemented here, signals
     to caller that K8s path is excluded).
   - ``macos`` → implies ``self-hosted``.
   - ``astrolift`` → K8s only; never runners.
3. Find all active, managed TenantClusters for the org whose
   node_os / node_arch / node_labels satisfy the selector.
4. Return the first match (cluster-over-runner priority) or None.

This module is a pure routing function — no side effects, no DB writes.
The Temporal activity layer handles retries and PENDING state.

Runner matching (M7b) is not yet implemented; only the K8s path is here.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrolift_clusters.models import TenantCluster
    from astrolift_identity.models import Organization


# ---------------------------------------------------------------------------
# Constants — canonical special labels (case-insensitive)
# ---------------------------------------------------------------------------

LABEL_SELF_HOSTED = "self-hosted"
LABEL_ASTROLIFT = "astrolift"
LABEL_MACOS = "macos"
LABEL_WINDOWS = "windows"
LABEL_DEFAULT = "astrolift/default"


# ---------------------------------------------------------------------------
# Dataclass: routing result
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class RoutingResult:
    """Outcome of a routing decision.

    Attributes:
        cluster: The matched TenantCluster, or None if no match.
        reason: Human-readable explanation for logging / error surfacing.
        runner_only: True when ``runs_on`` forces the runner path (self-hosted
            or macos labels).  The caller is responsible for the runner path.
    """

    cluster: TenantCluster | None
    reason: str
    runner_only: bool = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalise_labels(runs_on: str | list[str]) -> frozenset[str]:
    """Return a normalised (lowercase, stripped) frozenset of label strings."""
    if isinstance(runs_on, str):
        # "cluster:prod" or "astrolift/default" or a single label
        parts = [runs_on]
    else:
        parts = list(runs_on)
    return frozenset(p.strip().lower() for p in parts if p.strip())


def _is_runner_only(labels: frozenset[str]) -> bool:
    """Return True when the label set forces the self-hosted runner path."""
    return LABEL_SELF_HOSTED in labels or LABEL_MACOS in labels


def _cluster_matches(cluster: TenantCluster, labels: frozenset[str]) -> bool:
    """Return True when the cluster's capabilities satisfy the label selector.

    Matching rules:
    - OS labels (linux, windows, macos): if any OS label is in the selector,
      the cluster's node_os must match one of them.
    - Arch labels (amd64, arm64): if either appears in the selector, the
      cluster's node_arch must match.
    - Remaining labels: all must appear in cluster.node_labels
      (case-insensitive).
    - Special routing labels (self-hosted, astrolift, astrolift/default,
      cluster:*) are consumed by the router before this function is called.
    """
    os_labels = {"linux", "windows", "macos"}
    arch_labels = {"amd64", "arm64"}

    required_os = labels & os_labels
    required_arch = labels & arch_labels
    # Remaining labels after stripping OS, arch, and routing meta-labels.
    meta_labels = (
        {
            LABEL_SELF_HOSTED,
            LABEL_ASTROLIFT,
            LABEL_MACOS,
            LABEL_DEFAULT,
        }
        | os_labels
        | arch_labels
    )
    custom_required = labels - meta_labels - {lbl for lbl in labels if lbl.startswith("cluster:")}

    if required_os and cluster.node_os not in required_os:
        return False

    if required_arch and cluster.node_arch not in required_arch:
        return False

    # node_labels on the cluster model is a list of strings.
    cluster_label_set = frozenset(lbl.strip().lower() for lbl in (cluster.node_labels or []))
    if not custom_required.issubset(cluster_label_set):
        return False

    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def select_cluster(
    runs_on: str | list[str],
    org: Organization,
) -> TenantCluster | None:
    """Select a TenantCluster for the job's ``runs_on`` selector.

    Matches against active, managed clusters belonging to ``org``.

    Returns the matched cluster or None when no cluster satisfies the
    selector (caller should set the job status to PENDING and retry on
    a backoff schedule).

    Raises ``ValueError`` if ``runs_on`` is malformed.
    """
    result = route(runs_on, org)
    return result.cluster


def route(
    runs_on: str | list[str],
    org: Organization,
) -> RoutingResult:
    """Full routing decision with reason string.

    Callers that only need the cluster can use ``select_cluster`` instead.
    """
    from astrolift_clusters.models import TenantCluster

    labels = _normalise_labels(runs_on)

    if not labels:
        return RoutingResult(
            cluster=None,
            reason="runs_on is empty; cannot route.",
        )

    # --- Self-hosted / macOS → runner-only path ---
    if _is_runner_only(labels):
        return RoutingResult(
            cluster=None,
            reason=(
                "runs_on contains 'self-hosted' or 'macos' — routing to "
                "self-hosted runner path (K8s clusters excluded)."
            ),
            runner_only=True,
        )

    # --- Base queryset: active, managed, belonging to org ---
    qs = TenantCluster.objects.filter(
        organization=org,
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED,
    )

    # --- "astrolift/default" → any active managed cluster ---
    if LABEL_DEFAULT in labels or labels == {LABEL_ASTROLIFT}:
        cluster = qs.order_by("created_at").first()
        if cluster is None:
            return RoutingResult(
                cluster=None,
                reason="No active managed clusters found for org.",
            )
        return RoutingResult(
            cluster=cluster,
            reason=f"Matched default cluster '{cluster.slug}'.",
        )

    # --- "cluster:<name>" → lookup by name or slug ---
    cluster_directives = [lbl for lbl in labels if lbl.startswith("cluster:")]
    if cluster_directives:
        target_name = cluster_directives[0][len("cluster:") :]
        cluster = qs.filter(slug=target_name).first() or qs.filter(name__iexact=target_name).first()
        if cluster is None:
            return RoutingResult(
                cluster=None,
                reason=f"No active managed cluster named '{target_name}' found for org.",
            )
        return RoutingResult(
            cluster=cluster,
            reason=f"Matched cluster by name '{cluster.slug}'.",
        )

    # --- Label matching against node_os / node_arch / node_labels ---
    for cluster in qs.order_by("created_at"):
        if _cluster_matches(cluster, labels):
            return RoutingResult(
                cluster=cluster,
                reason=(f"Cluster '{cluster.slug}' satisfies label selector " f"{sorted(labels)!r}."),
            )

    return RoutingResult(
        cluster=None,
        reason=(
            f"No active managed cluster satisfies label selector " f"{sorted(labels)!r} for org '{org.slug}'."
        ),
    )
