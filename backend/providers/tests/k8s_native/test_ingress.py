"""Tests for k8s-native multi-variant IngressDriver (#49 + #8 + #852)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from _sdk.cluster import ApplyResult, DeleteResult
from k8s_native.ingress import (
    SUPPORTED_VARIANTS,
    K8sIngressConfig,
    K8sIngressDriver,
    OIDCAuthConfig,
)


def test_unknown_variant_rejected() -> None:
    with pytest.raises(ValueError):
        K8sIngressDriver(config=K8sIngressConfig(variant="unknown"))


@pytest.mark.parametrize("variant", SUPPORTED_VARIANTS)
def test_render_returns_at_least_one_manifest(variant: str) -> None:
    driver = K8sIngressDriver(config=K8sIngressConfig(variant=variant))
    manifests = driver.render_ingress(
        app="acme",
        workload="api",
        hostnames=["api.acme.example"],
        tls_strategy="letsencrypt",
    )
    assert len(manifests) >= 1


# ---- nginx --------------------------------------------------------


def test_nginx_emits_ingress_with_class() -> None:
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="nginx_ingress",
            ingress_class_name="nginx",
        )
    )
    [ing] = driver.render_ingress(
        app="acme",
        workload="api",
        hostnames=["api.acme.example"],
        tls_strategy="letsencrypt",
    )
    assert ing["kind"] == "Ingress"
    assert ing["spec"]["ingressClassName"] == "nginx"


def test_nginx_letsencrypt_adds_cert_manager_annotation() -> None:
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="nginx_ingress",
            cert_manager_issuer="letsencrypt-staging",
        )
    )
    [ing] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="letsencrypt",
    )
    assert ing["metadata"]["annotations"]["cert-manager.io/cluster-issuer"] == "letsencrypt-staging"


def test_nginx_tls_block_for_secret_strategy() -> None:
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="nginx_ingress",
        )
    )
    [ing] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="letsencrypt",
    )
    assert "tls" in ing["spec"]
    assert ing["spec"]["tls"][0]["secretName"] == "a-w-tls"


# ---- traefik ------------------------------------------------------


def test_traefik_uses_traefik_ingress_class() -> None:
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="traefik",
        )
    )
    [ing] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="letsencrypt",
    )
    assert ing["spec"]["ingressClassName"] == "traefik"


# ---- kong ---------------------------------------------------------


def test_kong_uses_kong_ingress_class() -> None:
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="kong",
        )
    )
    [ing] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="letsencrypt",
    )
    assert ing["spec"]["ingressClassName"] == "kong"


# ---- gateway api -------------------------------------------------


def test_gateway_api_renders_httproute() -> None:
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="gateway_api",
        )
    )
    [route] = driver.render_ingress(
        app="acme",
        workload="api",
        hostnames=["api.acme.example"],
        tls_strategy="letsencrypt",
    )
    assert route["kind"] == "HTTPRoute"
    assert route["spec"]["hostnames"] == ["api.acme.example"]


def test_gateway_api_route_references_parent_gateway() -> None:
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="gateway_api",
            gateway_class_name="my-gateway",
        )
    )
    [route] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="letsencrypt",
    )
    parent = route["spec"]["parentRefs"][0]
    assert parent["name"] == "my-gateway"


# ---- istio --------------------------------------------------------


def test_istio_emits_gateway_and_virtualservice() -> None:
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="istio_gateway",
        )
    )
    manifests = driver.render_ingress(
        app="acme",
        workload="api",
        hostnames=["api.acme.example"],
        tls_strategy="letsencrypt",
    )
    kinds = {m["kind"] for m in manifests}
    assert kinds == {"Gateway", "VirtualService"}


def test_istio_gateway_https_when_tls() -> None:
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="istio_gateway",
        )
    )
    manifests = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="letsencrypt",
    )
    gateway = next(m for m in manifests if m["kind"] == "Gateway")
    server = gateway["spec"]["servers"][0]
    assert server["port"]["protocol"] == "HTTPS"
    assert server["tls"]["mode"] == "SIMPLE"


def test_istio_gateway_http_when_no_tls() -> None:
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="istio_gateway",
        )
    )
    manifests = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="off",
    )
    gateway = next(m for m in manifests if m["kind"] == "Gateway")
    server = gateway["spec"]["servers"][0]
    assert server["port"]["protocol"] == "HTTP"
    assert "tls" not in server


# ---- update / delete --------------------------------------------


def test_update_calls_apply() -> None:
    cluster_driver = MagicMock()
    cluster_driver.apply_manifests.return_value = ApplyResult(
        created=[],
        updated=["acme-api"],
        unchanged=[],
        errors=[],
    )
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="nginx_ingress",
            cluster_driver=cluster_driver,
        )
    )
    driver.update_ingress_host(
        cluster="x",
        namespace="ns",
        app="acme",
        workload="api",
        new_hostname="api-v2.acme.example",
    )
    cluster_driver.apply_manifests.assert_called_once()


def test_update_requires_cluster_driver() -> None:
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="nginx_ingress",
        )
    )
    with pytest.raises(RuntimeError, match="cluster_driver"):
        driver.update_ingress_host(
            cluster="x",
            namespace="ns",
            app="a",
            workload="w",
            new_hostname="x.example",
        )


def test_delete_attempts_all_variants() -> None:
    """Delete fires stubs for all variant kinds — best-effort."""
    cluster_driver = MagicMock()
    cluster_driver.delete_manifests.return_value = DeleteResult(
        deleted=["Ingress/acme-api"],
        not_found=["HTTPRoute/acme-api", "VirtualService/acme-api"],
        errors=[],
    )
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="nginx_ingress",
            cluster_driver=cluster_driver,
        )
    )
    driver.delete_ingress(
        cluster="x",
        namespace="ns",
        app="acme",
        workload="api",
    )
    cluster_driver.delete_manifests.assert_called_once()
    args, _ = cluster_driver.delete_manifests.call_args
    stubs = args[2]
    kinds = {s["kind"] for s in stubs}
    # All 3 variant kinds attempted
    assert kinds == {"Ingress", "HTTPRoute", "VirtualService"}


# ---- OIDC edge auth (#852) ----------------------------------------


def test_nginx_oidc_auth_injects_auth_url_annotation() -> None:
    """When oidc_auth is set, nginx Ingress carries the auth-url annotation."""
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="nginx_ingress",
            oidc_auth=OIDCAuthConfig(auth_proxy_host="auth.cluster.example.com"),
        )
    )
    [ing] = driver.render_ingress(
        app="acme",
        workload="api",
        hostnames=["api.acme.example"],
        tls_strategy="letsencrypt",
    )
    annotations = ing["metadata"]["annotations"]
    assert annotations["nginx.ingress.kubernetes.io/auth-url"] == ("https://auth.cluster.example.com/oauth2/auth")


def test_nginx_oidc_auth_injects_auth_signin_annotation() -> None:
    """auth-signin annotation points oauth2-proxy start endpoint with rd param."""
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="nginx_ingress",
            oidc_auth=OIDCAuthConfig(auth_proxy_host="auth.cluster.example.com"),
        )
    )
    [ing] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="letsencrypt",
    )
    annotations = ing["metadata"]["annotations"]
    assert annotations["nginx.ingress.kubernetes.io/auth-signin"] == (
        "https://auth.cluster.example.com/oauth2/start?rd=https://$host$escaped_request_uri"
    )


def test_nginx_oidc_auth_injects_response_headers_annotation() -> None:
    """auth-response-headers annotation carries the configured header list."""
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="nginx_ingress",
            oidc_auth=OIDCAuthConfig(auth_proxy_host="auth.cluster.example.com"),
        )
    )
    [ing] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="letsencrypt",
    )
    annotations = ing["metadata"]["annotations"]
    assert "nginx.ingress.kubernetes.io/auth-response-headers" in annotations
    headers = annotations["nginx.ingress.kubernetes.io/auth-response-headers"].split(",")
    assert "X-Auth-Request-User" in headers
    assert "X-Auth-Request-Email" in headers


def test_nginx_no_oidc_auth_omits_auth_annotations() -> None:
    """Without oidc_auth, no nginx auth annotations are emitted."""
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="nginx_ingress",
        )
    )
    [ing] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="letsencrypt",
    )
    annotations = ing["metadata"]["annotations"]
    assert "nginx.ingress.kubernetes.io/auth-url" not in annotations
    assert "nginx.ingress.kubernetes.io/auth-signin" not in annotations


def test_traefik_oidc_auth_injects_auth_annotations() -> None:
    """OIDC auth annotations also land on traefik Ingresses (same _render_nginx_style path)."""
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="traefik",
            oidc_auth=OIDCAuthConfig(auth_proxy_host="auth.cluster.example.com"),
        )
    )
    [ing] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="letsencrypt",
    )
    annotations = ing["metadata"]["annotations"]
    assert "nginx.ingress.kubernetes.io/auth-url" in annotations


def test_kong_oidc_auth_injects_auth_annotations() -> None:
    """OIDC auth annotations also land on Kong Ingresses."""
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="kong",
            oidc_auth=OIDCAuthConfig(auth_proxy_host="auth.cluster.example.com"),
        )
    )
    [ing] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="letsencrypt",
    )
    annotations = ing["metadata"]["annotations"]
    assert "nginx.ingress.kubernetes.io/auth-url" in annotations


def test_oidc_auth_custom_response_headers() -> None:
    """Caller can override the response_headers tuple."""
    driver = K8sIngressDriver(
        config=K8sIngressConfig(
            variant="nginx_ingress",
            oidc_auth=OIDCAuthConfig(
                auth_proxy_host="auth.example.com",
                response_headers=("X-Custom-Header",),
            ),
        )
    )
    [ing] = driver.render_ingress(
        app="a",
        workload="w",
        hostnames=["x.example"],
        tls_strategy="letsencrypt",
    )
    annotations = ing["metadata"]["annotations"]
    assert annotations["nginx.ingress.kubernetes.io/auth-response-headers"] == "X-Custom-Header"
