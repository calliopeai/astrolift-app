"""The ingress controller's header buffers are sized for the auth gate (#1725).

Every app behind the central auth host carries a session cookie, and nginx
copies the whole ``Cookie`` header into the ``auth_request`` subrequest. At the
chart defaults (``large_client_header_buffers 4 8k``) a Cookie header past 8KB
is rejected by nginx itself, which reports it as an auth subrequest failure and
serves a bare 500 from the edge -- with nothing logged by oauth2-proxy, because
the subrequest never reached it.

That ceiling is hit in normal use, not by abuse: an oauth2-proxy session split
across ``_0``/``_1`` alongside the ``AWSELBAuthSessionCookie`` pair a cluster
leaves behind when it migrates off ALB auth is already ~16KB.
"""

from __future__ import annotations

from _sdk.cluster import ClusterContext
from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig


def _ingress_component():
    driver = K8sNativeClusterDriver(config=K8sNativeConfig())
    ctx = ClusterContext(slug="tenant", auth_method="kubeconfig")
    for component in driver.bootstrap_components(ctx):
        if component.key == "ingress-nginx":
            return component
    raise AssertionError("ingress-nginx component missing from the recipe")


def _controller_config():
    return _ingress_component().helm_values["controller"].get("config", {})


def test_client_header_buffers_exceed_the_8k_default():
    """A Cookie header over 8KB must not be rejected by the edge."""
    value = _controller_config().get("large-client-header-buffers")
    assert value is not None, "large-client-header-buffers must be set"
    count, size = value.split()
    assert size.endswith("k")
    assert int(size[:-1]) >= 16, f"{value} still rejects a ~16KB cookie header"
    assert int(count) >= 4


def test_proxy_buffer_size_exceeds_the_4k_default():
    """The auth subrequest carries the same cookies; its buffer has to hold
    them too, or the subrequest fails after the client request was accepted."""
    value = _controller_config().get("proxy-buffer-size")
    assert value is not None, "proxy-buffer-size must be set"
    assert value.endswith("k")
    assert int(value[:-1]) >= 8, f"{value} is below the 8KB the gate needs"


def test_metrics_serve_monitor_still_enabled():
    """The buffer settings are additive -- they must not displace the metrics
    config the edge golden signals are sourced from."""
    controller = _ingress_component().helm_values["controller"]
    assert controller["metrics"]["enabled"] is True
    assert controller["metrics"]["serviceMonitor"]["enabled"] is True
