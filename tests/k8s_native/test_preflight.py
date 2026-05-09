"""Tests for k8s_native operator pre-flight checks (#74)."""

from __future__ import annotations

from _sdk.cluster_capabilities import ClusterCapabilities
from k8s_native.preflight import REQUIREMENTS, preflight


def test_cnpg_preflight_passes_when_installed() -> None:
    caps = ClusterCapabilities(
        cluster_id="x", kubernetes_version="1.30",
        cnpg_installed=True,
    )
    report = preflight(
        kind="postgres", variant="cnpg", capabilities=caps,
    )
    assert report.ok is True
    assert report.failures == []


def test_cnpg_preflight_fails_when_missing() -> None:
    caps = ClusterCapabilities(
        cluster_id="x", kubernetes_version="1.30",
        cnpg_installed=False,
    )
    report = preflight(
        kind="postgres", variant="cnpg", capabilities=caps,
    )
    assert report.ok is False
    assert any(
        f.code == "missing_operator" for f in report.failures
    )
    assert "helm install cnpg" in report.install_hints[0]


def test_unknown_variant_fails() -> None:
    caps = ClusterCapabilities(
        cluster_id="x", kubernetes_version="1.30",
    )
    report = preflight(
        kind="postgres", variant="never", capabilities=caps,
    )
    assert report.ok is False
    assert report.failures[0].code == "unknown_variant"


def test_old_k8s_fails() -> None:
    caps = ClusterCapabilities(
        cluster_id="x",
        kubernetes_version="1.20",
        cnpg_installed=True,
    )
    report = preflight(
        kind="postgres", variant="cnpg", capabilities=caps,
    )
    assert report.ok is False
    assert any(f.code == "k8s_too_old" for f in report.failures)


def test_kafka_preflight_uses_operator_versions_dict() -> None:
    """Operators without dedicated bool flags fall back to the
    operator_versions dict."""
    caps = ClusterCapabilities(
        cluster_id="x", kubernetes_version="1.30",
        operator_versions={"strimzi-cluster-operator": "0.40.0"},
    )
    report = preflight(
        kind="event_stream", variant="kafka_strimzi",
        capabilities=caps,
    )
    assert report.ok is True


def test_nats_preflight_passes_with_no_operator_required() -> None:
    """NATS uses plain StatefulSets; no CRDs to require."""
    caps = ClusterCapabilities(
        cluster_id="x", kubernetes_version="1.30",
    )
    report = preflight(
        kind="event_stream", variant="nats", capabilities=caps,
    )
    assert report.ok is True


def test_all_registered_requirements_have_install_hints() -> None:
    """Every requirement entry must surface a hint — operators
    rely on these to fix failures fast."""
    for key, req in REQUIREMENTS.items():
        assert req.install_hint, (
            f"{key} requirement is missing an install_hint"
        )


def test_postgres_cnpg_required_crds_canonical() -> None:
    req = REQUIREMENTS[("postgres", "cnpg")]
    assert "clusters.postgresql.cnpg.io" in req.required_crds
