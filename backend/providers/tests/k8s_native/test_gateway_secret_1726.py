"""The gate stamps proof-of-passage on every authenticated request (#1726).

Before this, an app behind the central auth host could not tell a request that
came through the gate from anything else able to reach its Service port: nginx
forwarded ``X-Auth-Request-*`` and nothing attested where those headers came
from. A careful app refused everyone (qs-ops did exactly that); a careless one
would trust forged identity headers.

The secret is stamped by oauth2-proxy rather than by an Ingress annotation.
That choice is the point of the design: an annotation would need
``allow-snippet-annotations`` cluster-wide, which lets any Ingress author inject
arbitrary nginx config, and would put the secret in an object far from the one
that already holds the session.
"""

from __future__ import annotations

import base64

import pytest
from _sdk.cluster import ClusterContext
from k8s_native.central_auth import (
    CENTRAL_AUTH_ALPHA_CONFIG_SECRET_NAME,
    GATEWAY_SECRET_HEADER,
    alpha_config_document,
    central_auth_component,
)
from k8s_native.ingress import OIDCAuthConfig, nginx_auth_annotations

OIDC_CONFIG = {
    "discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc",
    "client_id": "1qqhi5irhften18gbbpailuo8v",
    "auth_proxy_host": "auth.astrolift.smdinfra.net",
}


# ---- the alpha config document ---------------------------------------


def test_gateway_secret_is_a_static_value_not_a_claim():
    """Its point is to prove the request passed through this proxy, which is a
    property of the path, not of the user."""
    doc = alpha_config_document("s" * 32)
    assert f"name: {GATEWAY_SECRET_HEADER}" in doc
    assert f"value: {base64.b64encode(b's' * 32).decode()}" in doc


def test_identity_headers_survive_the_move_to_alpha_config():
    """--alpha-config replaces --set-xauthrequest, so the two headers that flag
    produced have to be declared here or every gated app loses its identity."""
    doc = alpha_config_document("s" * 32)
    assert "name: X-Auth-Request-User" in doc
    assert "name: X-Auth-Request-Email" in doc
    assert "claim: user" in doc
    assert "claim: email" in doc


def test_a_static_upstream_is_declared():
    """The auth host proxies nothing -- it answers /oauth2/auth for nginx's
    auth_request. alpha config with no upstreams section refuses to start."""
    doc = alpha_config_document("s" * 32)
    assert "upstreams:" in doc
    assert "staticCode: 202" in doc


# ---- the component ---------------------------------------------------


def _component():
    return central_auth_component(OIDC_CONFIG)


def test_set_xauthrequest_is_not_sent_alongside_alpha_config():
    """oauth2-proxy rejects both together; this is a startup failure, not a
    warning, so neither the flag nor the chart's config key may reappear."""
    component = _component()
    assert "set-xauthrequest" not in component.helm_values["extraArgs"]
    assert "setXauthrequest" not in component.helm_values["config"]


def test_alpha_config_is_mounted_from_its_own_secret():
    component = _component()
    volumes = component.helm_values["extraVolumes"]
    assert any(v["secret"]["secretName"] == CENTRAL_AUTH_ALPHA_CONFIG_SECRET_NAME for v in volumes)
    mount = component.helm_values["extraVolumeMounts"][0]
    assert component.helm_values["extraArgs"]["alpha-config"].startswith(mount["mountPath"])


def test_the_gateway_secret_never_reaches_helm_values():
    """helm_values is served to operators over GraphQL. The secret lives in a
    Secret the recipe references by name, exactly like cookie_secret."""
    rendered = repr(_component().helm_values)
    assert "gateway_secret" not in rendered
    assert base64.b64encode(b"s" * 32).decode() not in rendered


# ---- the ingress half ------------------------------------------------


def test_nginx_forwards_the_gateway_header():
    """nginx only forwards headers named in auth-response-headers. Stamping it
    at the proxy and not forwarding it leaves the app with identity headers and
    nothing attesting their origin."""
    annotations = nginx_auth_annotations(OIDCAuthConfig(auth_proxy_host="auth.example.com"))
    forwarded = annotations["nginx.ingress.kubernetes.io/auth-response-headers"]
    assert GATEWAY_SECRET_HEADER in forwarded.split(",")


def test_identity_headers_are_still_forwarded():
    annotations = nginx_auth_annotations(OIDCAuthConfig(auth_proxy_host="auth.example.com"))
    forwarded = annotations["nginx.ingress.kubernetes.io/auth-response-headers"].split(",")
    assert "X-Auth-Request-User" in forwarded
    assert "X-Auth-Request-Email" in forwarded


@pytest.mark.parametrize("secret", ["", "short"])
def test_document_renders_for_any_secret_the_caller_supplies(secret):
    """Validation of secret strength belongs to whoever mints it; the renderer
    must not silently drop the header and leave a gate that stamps nothing."""
    doc = alpha_config_document(secret)
    assert f"name: {GATEWAY_SECRET_HEADER}" in doc
