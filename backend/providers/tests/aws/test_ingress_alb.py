"""Tests for AWS ALB IngressDriver (#30)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from _sdk.cluster import ApplyResult, DeleteResult
from aws.ingress_alb import ALBConfig, ALBIngressDriver


def test_render_basic_ingress_shape() -> None:
    driver = ALBIngressDriver(config=ALBConfig(region="us-east-1"))
    [ingress] = driver.render_ingress(
        app="acme",
        workload="api",
        hostnames=["api.acme.example"],
        tls_strategy="acm_dns_validated",
    )
    assert ingress["kind"] == "Ingress"
    assert ingress["metadata"]["name"] == "acme-api"
    assert ingress["spec"]["ingressClassName"] == "alb"
    rules = ingress["spec"]["rules"]
    assert rules[0]["host"] == "api.acme.example"


def test_render_uses_listen_ports_annotation() -> None:
    driver = ALBIngressDriver(
        config=ALBConfig(
            region="us-east-1",
            listen_ports=(80, 443),
        )
    )
    [ingress] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="acm_dns_validated",
    )
    raw = ingress["metadata"]["annotations"]["alb.ingress.kubernetes.io/listen-ports"]
    parsed = json.loads(raw)
    # 443 -> HTTPS, 80 -> HTTP
    assert {"HTTPS": 443} in parsed
    assert {"HTTP": 80} in parsed


def test_render_internal_scheme() -> None:
    driver = ALBIngressDriver(
        config=ALBConfig(
            region="us-east-1",
            scheme="internal",
        )
    )
    [ingress] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="acm_dns_validated",
    )
    assert ingress["metadata"]["annotations"]["alb.ingress.kubernetes.io/scheme"] == "internal"


def test_render_target_type_ip_for_fargate() -> None:
    """Fargate-backed pods need target-type=ip (instance NodePort
    isn't available)."""
    driver = ALBIngressDriver(
        config=ALBConfig(
            region="us-east-1",
            target_type="ip",
        )
    )
    [ingress] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="acm_dns_validated",
    )
    assert ingress["metadata"]["annotations"]["alb.ingress.kubernetes.io/target-type"] == "ip"


def test_render_ssl_redirect_when_https_listener() -> None:
    """SSL redirect should be set when 443 is in listen_ports."""
    driver = ALBIngressDriver(
        config=ALBConfig(
            region="us-east-1",
            listen_ports=(80, 443),
            ssl_redirect=True,
        )
    )
    [ingress] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="acm_dns_validated",
    )
    assert ingress["metadata"]["annotations"]["alb.ingress.kubernetes.io/ssl-redirect"] == "443"


def test_render_no_ssl_redirect_when_disabled() -> None:
    """For HTTP-01 ACME, operator may disable redirect so port 80
    serves the challenge."""
    driver = ALBIngressDriver(
        config=ALBConfig(
            region="us-east-1",
            ssl_redirect=False,
        )
    )
    [ingress] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="letsencrypt",
    )
    assert "alb.ingress.kubernetes.io/ssl-redirect" not in ingress["metadata"]["annotations"]


def test_render_certificate_arn_when_acm_strategy() -> None:
    arn = "arn:aws:acm:us-east-1:123:certificate/abc"
    driver = ALBIngressDriver(
        config=ALBConfig(
            region="us-east-1",
            certificate_arn=arn,
        )
    )
    [ingress] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="acm_dns_validated",
    )
    assert ingress["metadata"]["annotations"]["alb.ingress.kubernetes.io/certificate-arn"] == arn


def test_render_no_certificate_arn_for_letsencrypt() -> None:
    """Cert-manager strategy uses a k8s Secret, not the ARN annotation."""
    driver = ALBIngressDriver(
        config=ALBConfig(
            region="us-east-1",
            certificate_arn="arn:aws:acm:us-east-1:123:certificate/abc",
        )
    )
    [ingress] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="letsencrypt",
    )
    # ARN-only annotation isn't emitted under letsencrypt
    assert "alb.ingress.kubernetes.io/certificate-arn" not in ingress["metadata"]["annotations"]


def test_render_multiple_hosts() -> None:
    driver = ALBIngressDriver(config=ALBConfig(region="us-east-1"))
    [ingress] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["api.acme.example", "api.alt.example"],
        tls_strategy="acm_dns_validated",
    )
    hosts = [r["host"] for r in ingress["spec"]["rules"]]
    assert hosts == ["api.acme.example", "api.alt.example"]


def test_render_tls_block_for_acm() -> None:
    driver = ALBIngressDriver(config=ALBConfig(region="us-east-1"))
    [ingress] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="acm_dns_validated",
    )
    assert "tls" in ingress["spec"]
    assert ingress["spec"]["tls"][0]["hosts"] == ["x.example"]


def test_render_no_tls_block_for_letsencrypt() -> None:
    """LE strategy means cert-manager populates the Secret; ALB
    LBC discovers via SDS rather than the spec.tls block."""
    driver = ALBIngressDriver(config=ALBConfig(region="us-east-1"))
    [ingress] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="letsencrypt",
    )
    assert "tls" not in ingress["spec"]


# ---- update_ingress_host ----------------------------------------


def test_update_ingress_host_calls_apply() -> None:
    cluster_driver = MagicMock()
    cluster_driver.apply_manifests.return_value = ApplyResult(
        created=[],
        updated=["acme-api"],
        unchanged=[],
        errors=[],
    )
    driver = ALBIngressDriver(
        config=ALBConfig(
            region="us-east-1",
            cluster_driver=cluster_driver,
        )
    )
    driver.update_ingress_host(
        cluster="aws-prod",
        namespace="acme",
        app="acme",
        workload="api",
        new_hostname="api-v2.acme.example",
    )
    cluster_driver.apply_manifests.assert_called_once()


def test_update_ingress_host_requires_cluster_driver() -> None:
    """Driver constructed without cluster_driver can render but
    not mutate."""
    driver = ALBIngressDriver(config=ALBConfig(region="us-east-1"))
    with pytest.raises(RuntimeError, match="cluster_driver"):
        driver.update_ingress_host(
            cluster="x",
            namespace="ns",
            app="a",
            workload="w",
            new_hostname="x.example",
        )


def test_update_propagates_apply_errors() -> None:
    cluster_driver = MagicMock()
    cluster_driver.apply_manifests.return_value = ApplyResult(
        created=[],
        updated=[],
        unchanged=[],
        errors=["forbidden: webhook denied"],
    )
    driver = ALBIngressDriver(
        config=ALBConfig(
            region="us-east-1",
            cluster_driver=cluster_driver,
        )
    )
    with pytest.raises(RuntimeError, match="webhook denied"):
        driver.update_ingress_host(
            cluster="x",
            namespace="ns",
            app="a",
            workload="w",
            new_hostname="x.example",
        )


# ---- delete_ingress --------------------------------------------


def test_delete_ingress_calls_delete() -> None:
    cluster_driver = MagicMock()
    cluster_driver.delete_manifests.return_value = DeleteResult(
        deleted=["acme-api"],
        not_found=[],
        errors=[],
    )
    driver = ALBIngressDriver(
        config=ALBConfig(
            region="us-east-1",
            cluster_driver=cluster_driver,
        )
    )
    driver.delete_ingress(
        cluster="x",
        namespace="acme",
        app="acme",
        workload="api",
    )
    cluster_driver.delete_manifests.assert_called_once()


def test_delete_propagates_errors() -> None:
    cluster_driver = MagicMock()
    cluster_driver.delete_manifests.return_value = DeleteResult(
        deleted=[],
        not_found=[],
        errors=["forbidden"],
    )
    driver = ALBIngressDriver(
        config=ALBConfig(
            region="us-east-1",
            cluster_driver=cluster_driver,
        )
    )
    with pytest.raises(RuntimeError, match="forbidden"):
        driver.delete_ingress(
            cluster="x",
            namespace="ns",
            app="a",
            workload="w",
        )
