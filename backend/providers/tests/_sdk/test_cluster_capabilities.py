"""Tests for ClusterCapabilities + feature-probe (#61)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from _sdk.cluster_capabilities import (
    ClusterCapabilities,
    probe_capabilities,
    render_capability_summary,
)


@pytest.fixture
def fake_k8s() -> MagicMock:
    client = MagicMock()
    client.list.return_value = []
    return client


def _crds(*names: str) -> list[dict]:
    return [{"metadata": {"name": n}} for n in names]


def _deployments(*names: str) -> list[dict]:
    return [{"metadata": {"name": n}} for n in names]


def test_probe_detects_vpa_when_crd_and_recommender_present(
    fake_k8s: MagicMock,
) -> None:
    fake_k8s.list.side_effect = lambda kind, namespace=None: {
        "CustomResourceDefinition": _crds(
            "verticalpodautoscalers.autoscaling.k8s.io",
        ),
        "Deployment": _deployments("vpa-recommender", "other"),
    }[kind]
    caps = probe_capabilities(
        cluster_id="aws-prod",
        k8s_client=fake_k8s,
        kubernetes_version="1.30",
    )
    assert caps.vpa_installed is True


def test_probe_skips_vpa_when_only_crd_present(
    fake_k8s: MagicMock,
) -> None:
    """CRD present without recommender = nothing reconciles VPA
    objects. Don't claim VPA support."""
    fake_k8s.list.side_effect = lambda kind, namespace=None: {
        "CustomResourceDefinition": _crds(
            "verticalpodautoscalers.autoscaling.k8s.io",
        ),
        "Deployment": _deployments("other"),
    }[kind]
    caps = probe_capabilities(
        cluster_id="aws-prod",
        k8s_client=fake_k8s,
    )
    assert caps.vpa_installed is False


def test_probe_detects_cnpg_via_crd(
    fake_k8s: MagicMock,
) -> None:
    fake_k8s.list.side_effect = lambda kind, namespace=None: {
        "CustomResourceDefinition": _crds("clusters.postgresql.cnpg.io"),
        "Deployment": [],
    }[kind]
    caps = probe_capabilities(
        cluster_id="x",
        k8s_client=fake_k8s,
    )
    assert caps.cnpg_installed is True


def test_probe_detects_gateway_api(fake_k8s: MagicMock) -> None:
    fake_k8s.list.side_effect = lambda kind, namespace=None: {
        "CustomResourceDefinition": _crds(
            "gateways.gateway.networking.k8s.io",
            "httproutes.gateway.networking.k8s.io",
        ),
        "Deployment": [],
    }[kind]
    caps = probe_capabilities(
        cluster_id="x",
        k8s_client=fake_k8s,
    )
    assert caps.gateway_api_installed is True


def test_probe_detects_external_dns_via_deployment(
    fake_k8s: MagicMock,
) -> None:
    fake_k8s.list.side_effect = lambda kind, namespace=None: {
        "CustomResourceDefinition": [],
        "Deployment": _deployments("external-dns"),
    }[kind]
    caps = probe_capabilities(
        cluster_id="x",
        k8s_client=fake_k8s,
    )
    assert caps.external_dns_installed is True


def test_probe_safe_on_failing_list(fake_k8s: MagicMock) -> None:
    """If the list call raises (RBAC denied, transient API error),
    return capabilities all-False rather than blow up the probe."""
    fake_k8s.list.side_effect = RuntimeError("forbidden")
    caps = probe_capabilities(
        cluster_id="x",
        k8s_client=fake_k8s,
    )
    assert caps.vpa_installed is False
    assert caps.gateway_api_installed is False


def test_render_capability_summary() -> None:
    caps = ClusterCapabilities(
        cluster_id="x",
        kubernetes_version="1.30",
        vpa_installed=True,
        gateway_api_installed=True,
        cnpg_installed=True,
        cert_manager_installed=True,
        network_policy_engine="cilium",
        pod_security_admission_level="restricted",
    )
    summary = render_capability_summary(caps)
    assert "k8s 1.30" in summary
    assert "VPA" in summary
    assert "Gateway API" in summary
    assert "CNPG" in summary
    assert "cert-manager" in summary
    assert "netpol=cilium" in summary
    assert "PSA=restricted" in summary


def test_render_summary_minimal_cluster() -> None:
    caps = ClusterCapabilities(
        cluster_id="x",
        kubernetes_version="1.29",
    )
    summary = render_capability_summary(caps)
    assert summary.startswith("k8s 1.29")
    assert "VPA" not in summary  # not installed; not listed
