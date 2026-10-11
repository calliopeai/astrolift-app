"""The central auth host serves Astrolift-branded sign-in and error pages (#2285)."""

from __future__ import annotations

import re

import pytest

from k8s_native.central_auth import central_auth_component
from k8s_native.central_auth_pages import (
    ERROR_HTML,
    SIGN_IN_HTML,
    TEMPLATES_CONFIGMAP,
    TEMPLATES_DIR,
)

CONFIG = {
    "auth_proxy_host": "auth.astro.example.com",
    "client_id": "cid",
    "discovery_url": "https://idp.example.com/.well-known/openid-configuration",
}

# What oauth2-proxy 7.6 (chart 7.7.14) passes each template; any other field
# fails at render time and leaves a person on a blank page.
SIGN_IN_FIELDS = {
    "ProviderName",
    "SignInMessage",
    "StatusCode",
    "CustomLogin",
    "Redirect",
    "Version",
    "ProxyPrefix",
    "Footer",
    "LogoData",
}
ERROR_FIELDS = {"Title", "Message", "ProxyPrefix", "StatusCode", "Redirect", "RequestID", "Footer", "Version"}


def test_the_release_mounts_the_branded_templates_and_points_the_proxy_at_them():
    values = central_auth_component(CONFIG).helm_values

    assert values["extraArgs"]["custom-templates-dir"] == TEMPLATES_DIR
    [configmap] = values["extraObjects"]
    assert configmap["metadata"]["name"] == TEMPLATES_CONFIGMAP
    assert set(configmap["data"]) == {"sign_in.html", "error.html"}
    [volume] = values["extraVolumes"]
    [mount] = values["extraVolumeMounts"]
    assert volume["configMap"]["name"] == TEMPLATES_CONFIGMAP
    assert (mount["name"], mount["mountPath"], mount["readOnly"]) == (volume["name"], TEMPLATES_DIR, True)


@pytest.mark.parametrize("html, allowed", [(SIGN_IN_HTML, SIGN_IN_FIELDS), (ERROR_HTML, ERROR_FIELDS)])
def test_templates_use_only_fields_the_proxy_provides_and_load_nothing_external(html, allowed):
    assert set(re.findall(r"\.([A-Z][A-Za-z]+)", "".join(re.findall(r"{{(.*?)}}", html)))) <= allowed
    assert "https://" not in html and "<span>Astrolift</span>" in html


def test_the_sign_in_form_still_starts_the_flow_with_the_return_address():
    assert 'action="{{ .ProxyPrefix }}/start"' in SIGN_IN_HTML
    assert 'name="rd" value="{{ .Redirect }}"' in SIGN_IN_HTML


def test_dex_names_astrolift_on_its_dark_theme():
    from _sdk.cluster import ClusterContext
    from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig

    driver = K8sNativeClusterDriver(config=K8sNativeConfig())
    [dex] = [
        c for c in driver.bootstrap_components(ClusterContext(slug="t", auth_method="kubeconfig")) if c.key == "dex"
    ]
    assert dex.helm_values["config"]["frontend"] == {"issuer": "Astrolift", "theme": "dark"}
