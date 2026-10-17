"""An app's nginx edge streams an upload of any size unless the app caps it (#2423).

ingress-nginx answers 413 to a request body over 1m before the app sees it,
spools the body to the controller's disk before proxying, and drops an
upstream connection that sits 60s between reads or writes. The Ingress
Astrolift rendered for an app set none of the keys that change this, so an
app on an nginx edge could not take an upload past 1 MB (SteadyMD's
abbott-cgm).
"""

from __future__ import annotations

from _sdk.cluster import ClusterContext
from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig
from k8s_native.ingress import K8sIngressConfig, K8sIngressDriver, OIDCAuthConfig

_KEY = "nginx.ingress.kubernetes.io/"


def _annotations(**config) -> dict[str, str]:
    driver = K8sIngressDriver(config=K8sIngressConfig(variant="nginx_ingress", **config))
    [ingress] = driver.render_ingress(
        app="abbott-cgm",
        workload="web",
        hostnames=["abbott-cgm.apps.example.com"],
        tls_strategy="letsencrypt",
    )
    return ingress["metadata"]["annotations"]


def test_an_app_ingress_sets_no_body_limit_and_streams_the_body():
    annotations = _annotations()

    assert annotations[_KEY + "proxy-body-size"] == "0"
    assert annotations[_KEY + "proxy-request-buffering"] == "off"
    assert annotations[_KEY + "proxy-read-timeout"] == "3600"
    assert annotations[_KEY + "proxy-send-timeout"] == "3600"


def test_the_app_max_body_size_replaces_only_the_body_size():
    annotations = _annotations(max_body_size="100m")

    assert annotations[_KEY + "proxy-body-size"] == "100m"
    assert annotations[_KEY + "proxy-request-buffering"] == "off"
    assert annotations[_KEY + "proxy-read-timeout"] == "3600"
    assert annotations[_KEY + "proxy-send-timeout"] == "3600"


def test_the_body_handling_sits_beside_the_auth_gate():
    """A gated app uploads through the same Ingress; neither set displaces the other."""
    annotations = _annotations(oidc_auth=OIDCAuthConfig(auth_proxy_host="auth.apps.example.com"))

    assert annotations[_KEY + "proxy-body-size"] == "0"
    assert annotations[_KEY + "auth-url"] == "https://auth.apps.example.com/oauth2/auth"
    assert annotations["cert-manager.io/cluster-issuer"] == "letsencrypt-prod"


def _controller_config() -> dict[str, str]:
    driver = K8sNativeClusterDriver(config=K8sNativeConfig())
    ctx = ClusterContext(slug="tenant", auth_method="kubeconfig")
    component = next(c for c in driver.bootstrap_components(ctx) if c.key == "ingress-nginx")
    return component.helm_values["controller"]["config"]


def test_the_native_recipe_sets_the_same_body_handling_for_the_whole_controller():
    """An Ingress rendered before #2423 has no body annotations; the controller
    default is what covers it once the recipe reconciles."""
    config = _controller_config()

    assert config["proxy-body-size"] == "0"
    assert config["proxy-request-buffering"] == "off"
    assert config["proxy-read-timeout"] == "3600"
    assert config["proxy-send-timeout"] == "3600"
