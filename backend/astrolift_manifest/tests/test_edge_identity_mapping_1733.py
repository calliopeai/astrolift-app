"""An app can declare how the gate's identity reaches it (#1733).

One live app needs the gate's identity headers under names its backend
chose, plus the shared secret on a header of its own. That existed only as
a `kubectl annotate` on the live Ingress: nothing recreated it, nothing
recorded it, and a path that recreated rather than patched the Ingress
lost it silently, after which the app refused every request.

The surface is a *mapping*, not a raw nginx snippet. A snippet is an
escape hatch into the ingress controller's config language -- it ties the
app to nginx, hands it a sharp edge, and ingress-nginx has been narrowing
`allow-snippet-annotations` for CVE reasons. The rename map covers the
observed case and stays portable.
"""

from __future__ import annotations

import pytest

from astrolift_manifest.normalize import normalize
from astrolift_manifest.parser import ManifestError, parse_raw
from providers.k8s_native.ingress import (
    PLATFORM_SNIPPET_BEGIN,
    OIDCAuthConfig,
    platform_gateway_snippet,
)

_BASE = """
name = "qs-ops"

[[workloads]]
name = "web"
kind = "deployment"
is_public = true
"""

_WITH_EDGE = (
    _BASE
    + """
[edge]
gateway_secret_header = "x-qsr-proxy-secret"

[edge.identity_headers]
user = "x-qsr-user-id"
email = "x-qsr-email"
"""
)


def _auth(**kw):
    return OIDCAuthConfig(auth_proxy_host="auth.example.com", **kw)


# ---------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------


def test_a_manifest_without_the_block_parses_unchanged():
    assert parse_raw(_BASE).edge is None


def test_the_block_parses_into_an_ordered_mapping():
    edge = parse_raw(_WITH_EDGE).edge

    assert edge.gateway_secret_header == "x-qsr-proxy-secret"
    assert edge.identity_headers == (("email", "x-qsr-email"), ("user", "x-qsr-user-id"))


def test_an_unknown_identity_is_refused():
    """A mapping for a header the gate never sends would render config
    that silently forwards nothing."""
    with pytest.raises(ManifestError, match="unknown edge identity"):
        parse_raw(_BASE + '\n[edge.identity_headers]\ngroups = "x-groups"\n')


def test_an_unknown_edge_key_is_refused():
    with pytest.raises(ManifestError, match="unknown edge key"):
        parse_raw(_BASE + '\n[edge]\nsnippet = "proxy_set_header x 1;"\n')


@pytest.mark.parametrize(
    "header",
    ['x qsr"', "x-qsr;\\ninjected", "", "x/qsr"],
)
def test_a_header_name_that_is_not_a_header_name_is_refused(header):
    """The value is written into the controller's config; anything that is
    not an RFC 7230 token is an injection point."""
    with pytest.raises(ManifestError):
        parse_raw(_BASE + f'\n[edge]\ngateway_secret_header = "{header}"\n')


def test_an_empty_block_is_refused():
    with pytest.raises(ManifestError, match="declares nothing"):
        parse_raw(_BASE + "\n[edge]\n")


# ---------------------------------------------------------------------
# normalization
# ---------------------------------------------------------------------


def test_a_manifest_without_the_block_serializes_unchanged():
    """No ``edge`` key at all, so the manifest hash of an existing app is
    untouched by the upgrade and nothing redeploys on it."""
    assert "edge" not in normalize(parse_raw(_BASE)).serialized


def test_the_block_survives_normalization():
    serialized = normalize(parse_raw(_WITH_EDGE)).serialized

    assert serialized["edge"] == {
        "gateway_secret_header": "x-qsr-proxy-secret",
        "identity_headers": [["email", "x-qsr-email"], ["user", "x-qsr-user-id"]],
    }


# ---------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------


def test_the_platform_block_is_unchanged_for_an_app_that_declares_nothing():
    snippet = platform_gateway_snippet(_auth(gateway_secret="s3cret"))

    assert 'proxy_set_header X-Astrolift-Gateway-Secret "s3cret";' in snippet
    assert "auth_request_set" not in snippet


def test_a_declared_secret_header_replaces_the_default_name():
    edge = normalize(parse_raw(_WITH_EDGE)).serialized["edge"]

    snippet = platform_gateway_snippet(_auth(gateway_secret="s3cret"), edge=edge)

    assert 'proxy_set_header x-qsr-proxy-secret "s3cret";' in snippet
    assert "X-Astrolift-Gateway-Secret" not in snippet


def test_declared_identities_render_as_auth_request_set_pairs():
    edge = normalize(parse_raw(_WITH_EDGE)).serialized["edge"]

    snippet = platform_gateway_snippet(_auth(gateway_secret="s3cret"), edge=edge)

    assert "auth_request_set $astrolift_edge_user $upstream_http_x_auth_request_user;" in snippet
    assert "proxy_set_header x-qsr-user-id $astrolift_edge_user;" in snippet
    assert "auth_request_set $astrolift_edge_email $upstream_http_x_auth_request_email;" in snippet
    assert "proxy_set_header x-qsr-email $astrolift_edge_email;" in snippet


def test_everything_rendered_sits_inside_the_platform_fence():
    """The fence is what lets #1726 compose with content the platform does
    not own; content outside it would be clobbered on the next reconcile."""
    edge = normalize(parse_raw(_WITH_EDGE)).serialized["edge"]

    snippet = platform_gateway_snippet(_auth(gateway_secret="s3cret"), edge=edge)
    body = snippet.split(PLATFORM_SNIPPET_BEGIN, 1)[1]

    for line in ("proxy_set_header x-qsr-user-id", "auth_request_set $astrolift_edge_user"):
        assert line in body


def test_identity_headers_render_without_a_gateway_secret():
    """The identity mapping and the proof-of-passage stamp are separate
    features; an install with no secret still forwards identity."""
    edge = normalize(parse_raw(_WITH_EDGE)).serialized["edge"]

    snippet = platform_gateway_snippet(_auth(), edge=edge)

    assert "proxy_set_header x-qsr-user-id $astrolift_edge_user;" in snippet
    assert "x-qsr-proxy-secret" not in snippet


def test_rendering_is_byte_stable():
    """Re-rendering must not churn the annotation, or every reconcile
    writes a new Ingress revision."""
    edge = normalize(parse_raw(_WITH_EDGE)).serialized["edge"]

    first = platform_gateway_snippet(_auth(gateway_secret="s3cret"), edge=edge)
    second = platform_gateway_snippet(_auth(gateway_secret="s3cret"), edge=edge)

    assert first == second
