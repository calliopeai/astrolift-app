"""The EKS recipe offers the Envoy edge, and an ALB-only cluster keeps its recipe (#2055)."""

from __future__ import annotations

from unittest.mock import MagicMock

from _sdk.cluster import ClusterContext
from aws.cluster_eks import EKSClusterDriver, EKSConfig

COGNITO = {
    "discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc/.well-known/openid-configuration",
    "client_id": "central-client-id",
    "auth_proxy_host": "auth.astro.example.net",
}


def _driver() -> EKSClusterDriver:
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Account": "123456789012"}
    eks = MagicMock()
    eks.describe_cluster.return_value = {"cluster": {"resourcesVpcConfig": {"vpcId": "vpc-123"}}}
    ec2 = MagicMock()
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    return EKSClusterDriver(
        config=EKSConfig(region="us-west-2", cluster_name="astrolift-eks"),
        eks_client=eks,
        sts_client=sts,
        ec2_client=ec2,
        k8s_client_factory=lambda **kw: MagicMock(),
    )


def _component(ctx: ClusterContext):
    [edge] = [c for c in _driver().bootstrap_components(ctx) if c.key == "envoy-gateway"]
    return edge


def test_prometheus_recipe_scrapes_the_owned_gateway_proxy_across_namespaces():
    ctx = ClusterContext(slug="c", auth_method="exec_plugin", ingress_class="envoy", oidc_auth_config=COGNITO)
    component = next(c for c in _driver().bootstrap_components(ctx) if c.key == "kube-prometheus-stack")
    [monitor] = component.helm_values["prometheus"]["additionalPodMonitors"]
    assert monitor["namespaceSelector"] == {"any": True}
    assert monitor["selector"]["matchLabels"] == {
        "app.kubernetes.io/name": "envoy",
        "app.kubernetes.io/component": "proxy",
        "gateway.envoyproxy.io/owning-gateway-namespace": "astrolift-edge",
        "gateway.envoyproxy.io/owning-gateway-name": "edge",
    }
    assert monitor["podMetricsEndpoints"] == [{"port": "metrics", "path": "/stats/prometheus", "interval": "15s"}]


def test_alb_only_cluster_does_not_enable_the_edge():
    ctx = ClusterContext(slug="c", auth_method="exec_plugin", ingress_class="alb")
    edge = _component(ctx)
    assert edge.default_enabled is False
    assert not [m for m in edge.post_install_manifests if m["kind"] in ("SecurityPolicy", "Ingress")]


def test_envoy_cluster_enables_the_edge_behind_an_alb():
    ctx = ClusterContext(slug="c", auth_method="exec_plugin", ingress_class="envoy", oidc_auth_config=COGNITO)
    edge = _component(ctx)
    assert edge.default_enabled is True
    assert edge.chart_repo_type == "oci"
    assert edge.chart_repo_url == "oci://docker.io/envoyproxy"
    assert edge.chart_name == "gateway-helm"
    [ingress] = [m for m in edge.post_install_manifests if m["kind"] == "Ingress"]
    assert ingress["metadata"]["namespace"] == "astrolift-system"
    assert ingress["spec"]["rules"][0]["host"] == "*.astro.example.net"
    assert [m for m in edge.post_install_manifests if m["kind"] == "SecurityPolicy"]
