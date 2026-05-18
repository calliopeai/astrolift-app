"""Tests for AzureAppGatewayIngressDriver (#43)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from azure.ingress_appgw import (
    AppGatewayIngressConfig,
    AzureAppGatewayIngressDriver,
)


def test_unsupported_variant() -> None:
    with pytest.raises(ValueError, match="not in"):
        AzureAppGatewayIngressDriver(
            config=AppGatewayIngressConfig(variant="unknown"),
        )


def test_agic_renders_with_appgw_class() -> None:
    driver = AzureAppGatewayIngressDriver(
        config=AppGatewayIngressConfig(
            variant="agic",
            akv_secret_id=(
                "https://vault.azure.net/secrets/wildcard-acme-com"
            ),
        ),
    )
    manifests = driver.render_ingress(
        app="acme", workload="api",
        hostnames=["api.acme.com"], tls_strategy="akv_referenced",
    )
    assert len(manifests) == 1
    m = manifests[0]
    assert m["apiVersion"] == "networking.k8s.io/v1"
    assert m["kind"] == "Ingress"
    annos = m["metadata"]["annotations"]
    assert (
        annos["kubernetes.io/ingress.class"]
        == "azure/application-gateway"
    )
    assert (
        annos["appgw.ingress.kubernetes.io/appgw-ssl-certificate"]
        == "https://vault.azure.net/secrets/wildcard-acme-com"
    )


def test_agic_minimal_no_akv() -> None:
    driver = AzureAppGatewayIngressDriver(
        config=AppGatewayIngressConfig(variant="agic"),
    )
    manifests = driver.render_ingress(
        app="acme", workload="api",
        hostnames=["api.acme.com"], tls_strategy="provided",
    )
    annos = manifests[0]["metadata"]["annotations"]
    assert "appgw.ingress.kubernetes.io/appgw-ssl-certificate" not in annos


def test_gateway_api_emits_httproute() -> None:
    driver = AzureAppGatewayIngressDriver(
        config=AppGatewayIngressConfig(variant="gateway_api"),
    )
    manifests = driver.render_ingress(
        app="acme", workload="api",
        hostnames=["api.acme.com", "www.acme.com"],
        tls_strategy="akv_referenced",
    )
    m = manifests[0]
    assert m["apiVersion"] == "gateway.networking.k8s.io/v1"
    assert m["kind"] == "HTTPRoute"
    assert m["spec"]["hostnames"] == ["api.acme.com", "www.acme.com"]


def test_update_ingress_host_requires_cluster_driver() -> None:
    driver = AzureAppGatewayIngressDriver(
        config=AppGatewayIngressConfig(variant="agic"),
    )
    with pytest.raises(RuntimeError, match="cluster_driver"):
        driver.update_ingress_host(
            cluster="c", namespace="n", app="a", workload="w",
            new_hostname="x.com",
        )


def test_delete_ingress_strips_both_kinds() -> None:
    cluster_driver = MagicMock()
    cluster_driver.delete_manifests.return_value = MagicMock(errors=[])
    driver = AzureAppGatewayIngressDriver(
        config=AppGatewayIngressConfig(
            variant="agic", cluster_driver=cluster_driver,
        ),
    )
    driver.delete_ingress(
        cluster="c", namespace="ns", app="acme", workload="api",
    )
    args, kwargs = cluster_driver.delete_manifests.call_args
    stubs = args[2]
    kinds = {s["kind"] for s in stubs}
    assert kinds == {"Ingress", "HTTPRoute"}
