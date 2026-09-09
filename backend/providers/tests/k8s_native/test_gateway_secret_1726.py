"""The gate stamps proof-of-passage on requests it admits (#1726).

Before this, an app behind the central auth host could not tell a request that
came through the gate from anything else able to reach its Service port: nginx
forwarded ``X-Auth-Request-*`` and nothing attested where those headers came
from. qs-ops refused everyone on exactly that basis; a less careful app would
trust identity headers any pod could forge.

An earlier attempt injected the header from oauth2-proxy via ``--alpha-config``.
That does not start: the chart emits ``--http-address`` unconditionally and
oauth2-proxy rejects it once alpha config is in play. It is reverted, and the
header is stamped at the Ingress instead.
"""

from __future__ import annotations

from k8s_native.ingress import OIDCAuthConfig, nginx_auth_annotations

SNIPPET = "nginx.ingress.kubernetes.io/configuration-snippet"
SECRET = "s" * 64


def _annotations(**kw):
    return nginx_auth_annotations(OIDCAuthConfig(auth_proxy_host="auth.example.com", **kw))


def test_the_secret_is_stamped_when_configured():
    snippet = _annotations(gateway_secret=SECRET)[SNIPPET]
    assert "proxy_set_header X-Astrolift-Gateway-Secret" in snippet
    assert SECRET in snippet


def test_the_header_is_set_not_copied_from_the_client():
    """What makes it unforgeable: whatever a caller sends under this name is
    overwritten before the app sees it. A snippet that read $http_* would
    forward the client's own value and prove nothing."""
    snippet = _annotations(gateway_secret=SECRET)[SNIPPET]
    assert "$http_" not in snippet
    assert snippet.strip().startswith("proxy_set_header")


def test_no_secret_means_no_header_rather_than_a_blank_one():
    """A blank header reads as "the gate vouched for this" to an app testing
    presence rather than value."""
    assert SNIPPET not in _annotations()


def test_the_gate_still_renders_without_a_secret():
    """gateway_secret is incremental hardening. A cluster that has not set one
    keeps its gate -- requiring it would take auth off every app on that
    cluster."""
    annotations = _annotations()
    assert annotations["nginx.ingress.kubernetes.io/auth-url"].endswith("/oauth2/auth")
    assert "X-Auth-Request-Email" in annotations["nginx.ingress.kubernetes.io/auth-response-headers"]


def test_a_custom_header_name_is_honoured():
    snippet = _annotations(gateway_secret=SECRET, gateway_secret_header="x-qsr-proxy-secret")[SNIPPET]
    assert "proxy_set_header x-qsr-proxy-secret" in snippet


def test_the_snippet_key_is_owned_by_the_live_patcher():
    """core.ingress_reconcile patches exactly NGINX_AUTH_ANNOTATION_KEYS. If the
    snippet is not in that set, a reconcile refreshes the auth-* keys and leaves
    a stale secret behind -- the app then compares against one the gate no
    longer sends and every request fails closed."""
    from k8s_native.ingress import NGINX_AUTH_ANNOTATION_KEYS

    assert SNIPPET in NGINX_AUTH_ANNOTATION_KEYS
