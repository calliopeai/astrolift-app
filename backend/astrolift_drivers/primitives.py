"""
Non-managed-service portable-primitives resolution (#62, spec 20 §7-9).

Three resolvers for the things that aren't managed services but
still vary across clouds:

* **Storage class** — performance tier (`standard / balanced /
  high_iops / extreme`) maps to a per-cluster k8s StorageClass.
* **Workload identity** — fully abstracted from the manifest; the
  resolver picks IRSA / Workload Identity / Federated Credentials /
  projected SA tokens based on cluster type.
* **Ingress kind** — defaults to the cluster's default ingress
  class; manifest pin like ``aws/alb`` or ``gateway_api`` overrides.

Pure-Python. Each resolver consumes a small dataclass projection
of the cluster (caller flattens TenantCluster + plugin manifests
into these) and produces a concrete native value.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from enum import StrEnum

# ---- storage class --------------------------------------------------


class PerformanceTier(StrEnum):
    STANDARD = "standard"
    BALANCED = "balanced"
    HIGH_IOPS = "high_iops"
    EXTREME = "extreme"


class StorageResolutionError(ValueError):
    pass


def parse_tier(value: str | None) -> PerformanceTier:
    """Parse a manifest's ``performance_tier`` field. None / empty
    → BALANCED (sensible default — neither cheap nor expensive)."""
    if value is None or value == "":
        return PerformanceTier.BALANCED
    try:
        return PerformanceTier(value)
    except ValueError as exc:
        raise StorageResolutionError(
            f"performance_tier {value!r} not one of {[t.value for t in PerformanceTier]}"
        ) from exc


def resolve_storage_class(
    *,
    explicit_class: str,
    tier: PerformanceTier,
    tier_to_class: Mapping[PerformanceTier, str],
    default_class: str,
) -> str:
    """Pick the concrete storage class for this volume.

    Order:
      1. Manifest's explicit ``storage_class = "..."`` (escape hatch
         for operators who know exactly what they want).
      2. Tier mapping declared by the cluster's plugin.
      3. Cluster default storage class.
      4. Raise — no default and no mapping → caller can't make a PV.
    """
    if explicit_class:
        return explicit_class
    if tier in tier_to_class:
        return tier_to_class[tier]
    if default_class:
        return default_class
    raise StorageResolutionError(f"no storage class for tier {tier.value!r} and no cluster default")


# ---- workload identity ----------------------------------------------


class WorkloadIdentityKind(StrEnum):
    """One of these per cluster. The manifest never references this —
    the resolver picks based on cluster type."""

    IRSA = "aws.irsa"
    GCP_WORKLOAD_IDENTITY = "gcp.workload_identity"
    AZURE_FEDERATED_CREDENTIAL = "azure.federated_credential"
    PROJECTED_SA = "k8s.projected_sa"
    """Vanilla k8s + on-prem fallback. Not as strong as the cloud
    options (no automatic STS exchange) but works everywhere."""


_CLUSTER_TO_WIK: dict[str, WorkloadIdentityKind] = {
    "aws-eks": WorkloadIdentityKind.IRSA,
    "aws": WorkloadIdentityKind.IRSA,
    "gcp-gke": WorkloadIdentityKind.GCP_WORKLOAD_IDENTITY,
    "gcp": WorkloadIdentityKind.GCP_WORKLOAD_IDENTITY,
    "azure-aks": WorkloadIdentityKind.AZURE_FEDERATED_CREDENTIAL,
    "azure": WorkloadIdentityKind.AZURE_FEDERATED_CREDENTIAL,
    "vanilla-k8s": WorkloadIdentityKind.PROJECTED_SA,
    "onprem": WorkloadIdentityKind.PROJECTED_SA,
}


def resolve_workload_identity(*, cluster_provider: str) -> WorkloadIdentityKind:
    """Pick the workload identity mechanism for a cluster.

    Provider strings the platform recognises are listed in
    ``_CLUSTER_TO_WIK``. Unknown provider falls back to projected
    SA tokens — works everywhere, just not as strong."""
    return _CLUSTER_TO_WIK.get(cluster_provider.lower(), WorkloadIdentityKind.PROJECTED_SA)


# ---- ingress --------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class IngressDecision:
    kind: str  # 'aws/alb' / 'k8s/nginx' / 'gateway_api' / etc.
    source: str  # 'manifest' | 'cluster_default'


def resolve_ingress_kind(
    *,
    manifest_pin: str,
    cluster_default: str,
    cluster_supported: frozenset[str],
) -> IngressDecision:
    """Pick the ingress kind. Manifest pin wins if the cluster
    supports it; otherwise cluster default; raise on neither."""
    if manifest_pin:
        if manifest_pin not in cluster_supported:
            raise StorageResolutionError(
                f"ingress.kind={manifest_pin!r} not supported by this "
                f"cluster (supported: {sorted(cluster_supported)})"
            )
        return IngressDecision(kind=manifest_pin, source="manifest")

    if cluster_default and cluster_default in cluster_supported:
        return IngressDecision(kind=cluster_default, source="cluster_default")

    raise StorageResolutionError(
        f"no ingress.kind pin in manifest and no usable cluster default "
        f"(default={cluster_default!r}, supported={sorted(cluster_supported)})"
    )
