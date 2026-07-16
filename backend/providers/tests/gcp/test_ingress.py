"""Tests for GCPIngressDriver (#37)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gcp.ingress import GCPIngressConfig, GCPIngressDriver


def test_unsupported_variant_rejected() -> None:
    with pytest.raises(ValueError, match="not in"):
        GCPIngressDriver(config=GCPIngressConfig(variant="never_heard_of_it"))


def test_gce_ingress_renders_with_static_ip() -> None:
    driver = GCPIngressDriver(
        config=GCPIngressConfig(
            variant="gce_ingress",
            static_ip_name="prod-lb-ip",
            managed_cert_name="prod-cert",
        ),
    )
    manifests = driver.render_ingress(
        app="acme",
        workload="api",
        hostnames=["api.acme.com"],
        tls_strategy="gcp_managed_cert",
    )
    assert len(manifests) == 1
    m = manifests[0]
    assert m["apiVersion"] == "networking.k8s.io/v1"
    assert m["kind"] == "Ingress"
    annotations = m["metadata"]["annotations"]
    assert annotations["kubernetes.io/ingress.class"] == "gce"
    assert annotations["kubernetes.io/ingress.global-static-ip-name"] == "prod-lb-ip"
    assert annotations["networking.gke.io/managed-certificates"] == "prod-cert"
    rule = m["spec"]["rules"][0]
    assert rule["host"] == "api.acme.com"
    assert rule["http"]["paths"][0]["backend"]["service"]["name"] == "api"


def test_gce_ingress_minimal_no_static_ip_or_cert() -> None:
    driver = GCPIngressDriver(
        config=GCPIngressConfig(variant="gce_ingress"),
    )
    manifests = driver.render_ingress(
        app="acme",
        workload="api",
        hostnames=["api.acme.com"],
        tls_strategy="letsencrypt",
    )
    annotations = manifests[0]["metadata"]["annotations"]
    assert "kubernetes.io/ingress.global-static-ip-name" not in annotations
    assert "networking.gke.io/managed-certificates" not in annotations


def test_gateway_api_emits_httproute() -> None:
    driver = GCPIngressDriver(
        config=GCPIngressConfig(variant="gateway_api"),
    )
    manifests = driver.render_ingress(
        app="acme",
        workload="api",
        hostnames=["api.acme.com", "www.acme.com"],
        tls_strategy="gcp_managed_cert",
    )
    assert len(manifests) == 1
    m = manifests[0]
    assert m["apiVersion"] == "gateway.networking.k8s.io/v1"
    assert m["kind"] == "HTTPRoute"
    assert m["spec"]["hostnames"] == ["api.acme.com", "www.acme.com"]
    assert m["spec"]["parentRefs"][0]["name"] == "astrolift-gateway"


def test_update_ingress_host_requires_cluster_driver() -> None:
    driver = GCPIngressDriver(
        config=GCPIngressConfig(variant="gce_ingress"),
    )
    with pytest.raises(RuntimeError, match="cluster_driver"):
        driver.update_ingress_host(
            cluster="c",
            namespace="ns",
            app="acme",
            workload="api",
            new_hostname="new.acme.com",
        )


def test_update_ingress_host_applies_through_cluster_driver() -> None:
    cluster_driver = MagicMock()
    cluster_driver.apply_manifests.return_value = MagicMock(
        ok=True,
        errors=[],
    )
    driver = GCPIngressDriver(
        config=GCPIngressConfig(
            variant="gce_ingress",
            cluster_driver=cluster_driver,
        ),
    )
    driver.update_ingress_host(
        cluster="c",
        namespace="ns",
        app="acme",
        workload="api",
        new_hostname="new.acme.com",
    )
    cluster_driver.apply_manifests.assert_called_once()


def test_delete_ingress_strips_both_resource_kinds() -> None:
    cluster_driver = MagicMock()
    cluster_driver.delete_manifests.return_value = MagicMock(errors=[])
    driver = GCPIngressDriver(
        config=GCPIngressConfig(
            variant="gateway_api",
            cluster_driver=cluster_driver,
        ),
    )
    driver.delete_ingress(
        cluster="c",
        namespace="ns",
        app="acme",
        workload="api",
    )
    args, _kwargs = cluster_driver.delete_manifests.call_args
    stubs = args[2]
    kinds = {s["kind"] for s in stubs}
    assert kinds == {"Ingress", "HTTPRoute"}
