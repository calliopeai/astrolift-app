"""Tests for storage class / workload identity / ingress resolution (#62)."""

from __future__ import annotations

import pytest

from astrolift_drivers.primitives import (
    PerformanceTier,
    StorageResolutionError,
    WorkloadIdentityKind,
    parse_tier,
    resolve_ingress_kind,
    resolve_storage_class,
    resolve_workload_identity,
)

# ---- tier parsing ---------------------------------------------------


def test_parse_tier_default_balanced():
    assert parse_tier(None) == PerformanceTier.BALANCED
    assert parse_tier("") == PerformanceTier.BALANCED


def test_parse_tier_known():
    for t in PerformanceTier:
        assert parse_tier(t.value) == t


def test_parse_tier_rejects_unknown():
    with pytest.raises(StorageResolutionError, match="not one of"):
        parse_tier("ludicrous")


# ---- storage class --------------------------------------------------


def test_explicit_class_wins():
    out = resolve_storage_class(
        explicit_class="my-special-sc",
        tier=PerformanceTier.BALANCED,
        tier_to_class={PerformanceTier.BALANCED: "gp3"},
        default_class="gp2",
    )
    assert out == "my-special-sc"


def test_tier_mapping_used_when_no_explicit():
    out = resolve_storage_class(
        explicit_class="",
        tier=PerformanceTier.BALANCED,
        tier_to_class={
            PerformanceTier.STANDARD: "gp2",
            PerformanceTier.BALANCED: "gp3",
            PerformanceTier.HIGH_IOPS: "io2",
        },
        default_class="gp2",
    )
    assert out == "gp3"


def test_default_class_used_when_no_tier_mapping():
    out = resolve_storage_class(
        explicit_class="",
        tier=PerformanceTier.EXTREME,
        tier_to_class={PerformanceTier.BALANCED: "gp3"},  # no extreme
        default_class="gp2",
    )
    assert out == "gp2"


def test_raises_when_no_default_and_no_mapping():
    with pytest.raises(StorageResolutionError, match="no storage class"):
        resolve_storage_class(
            explicit_class="",
            tier=PerformanceTier.EXTREME,
            tier_to_class={},
            default_class="",
        )


# ---- workload identity ----------------------------------------------


def test_workload_identity_recognised_clouds():
    assert resolve_workload_identity(cluster_provider="aws-eks") == WorkloadIdentityKind.IRSA
    assert resolve_workload_identity(cluster_provider="gcp-gke") == WorkloadIdentityKind.GCP_WORKLOAD_IDENTITY
    assert resolve_workload_identity(cluster_provider="azure-aks") == WorkloadIdentityKind.AZURE_FEDERATED_CREDENTIAL


def test_workload_identity_short_provider_keys():
    """The bare 'aws' / 'gcp' / 'azure' tags also resolve correctly
    so a partial cluster record (provider known but not the
    managed-k8s flavor yet) doesn't fall through to projected-SA."""
    assert resolve_workload_identity(cluster_provider="aws") == WorkloadIdentityKind.IRSA
    assert resolve_workload_identity(cluster_provider="gcp") == WorkloadIdentityKind.GCP_WORKLOAD_IDENTITY
    assert resolve_workload_identity(cluster_provider="azure") == WorkloadIdentityKind.AZURE_FEDERATED_CREDENTIAL


def test_workload_identity_vanilla_k8s_uses_projected_sa():
    assert resolve_workload_identity(cluster_provider="vanilla-k8s") == WorkloadIdentityKind.PROJECTED_SA
    assert resolve_workload_identity(cluster_provider="onprem") == WorkloadIdentityKind.PROJECTED_SA


def test_workload_identity_unknown_provider_falls_back_safely():
    """Unknown cluster type still gets PROJECTED_SA — works
    everywhere, just not as strong."""
    assert resolve_workload_identity(cluster_provider="some-future-cloud") == WorkloadIdentityKind.PROJECTED_SA


def test_workload_identity_case_insensitive():
    assert resolve_workload_identity(cluster_provider="AWS-EKS") == WorkloadIdentityKind.IRSA


# ---- ingress --------------------------------------------------------


def test_manifest_pin_wins_when_supported():
    out = resolve_ingress_kind(
        manifest_pin="aws/alb",
        cluster_default="aws/alb",
        cluster_supported=frozenset({"aws/alb", "k8s/nginx"}),
    )
    assert out.kind == "aws/alb"
    assert out.source == "manifest"


def test_manifest_pin_unsupported_raises():
    with pytest.raises(StorageResolutionError, match="not supported"):
        resolve_ingress_kind(
            manifest_pin="azure/agw",
            cluster_default="aws/alb",
            cluster_supported=frozenset({"aws/alb"}),
        )


def test_falls_back_to_cluster_default_when_no_pin():
    out = resolve_ingress_kind(
        manifest_pin="",
        cluster_default="k8s/nginx",
        cluster_supported=frozenset({"k8s/nginx", "gateway_api"}),
    )
    assert out.kind == "k8s/nginx"
    assert out.source == "cluster_default"


def test_raises_when_no_pin_and_no_default():
    with pytest.raises(StorageResolutionError, match="no ingress.kind"):
        resolve_ingress_kind(
            manifest_pin="",
            cluster_default="",
            cluster_supported=frozenset({"k8s/nginx"}),
        )


def test_raises_when_default_not_in_supported():
    """Defensive: cluster default is misconfigured; surface loudly
    rather than emit a manifest pointing at a nonexistent class."""
    with pytest.raises(StorageResolutionError):
        resolve_ingress_kind(
            manifest_pin="",
            cluster_default="not-installed",
            cluster_supported=frozenset({"k8s/nginx"}),
        )
