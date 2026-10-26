"""A public app's route on the Envoy edge (#2504).

Public by its own route going without the gate label, never by loosening the
shared policy every gated app relies on. ``verification/edge-public-kind``
checks the same renders on a real Envoy Gateway.
"""

from __future__ import annotations

import itertools

import pytest

from k8s_native.edge_gateway import (
    DEFAULT_GATEWAY_SECRET_HEADER,
    GATE_LABEL,
    ID_TOKEN_HEADER,
    app_route_gated,
    edge_post_install_manifests,
    render_app_routes,
)

CFG = {
    "discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_x/.well-known/openid-configuration",
    "client_id": "c",
    "auth_proxy_host": "auth.edge.test",
}


@pytest.mark.parametrize(("allow", "public", "restricted", "brain"), list(itertools.product([True, False], repeat=4)))
def test_only_every_condition_together_takes_the_gate_off(allow, public, restricted, brain):
    gated = app_route_gated(
        cluster_gated=True, allow_public_apps=allow, edge_public=public, restricted=restricted, brain=brain
    )
    assert gated is not (allow and public and not restricted and not brain)


def test_a_cluster_without_a_gate_stays_ungated():
    assert (
        app_route_gated(cluster_gated=False, allow_public_apps=False, edge_public=False, restricted=True, brain=True)
        is False
    )


def _route(gated):
    out = render_app_routes(
        app_slug="pub",
        namespace="pub-ns",
        workloads={"web": (["pub.edge.test"], 8080)},
        gated=gated,
        paused=False,
        gateway_secret="gw" if gated else "",
    )
    [route] = [m for m in out if m["kind"] == "HTTPRoute"]
    return route


def test_a_public_route_has_no_gate_label_and_no_gateway_secret():
    route = _route(gated=False)
    assert GATE_LABEL not in route["metadata"]["labels"]
    modifier = route["spec"]["rules"][0]["filters"][0]["requestHeaderModifier"]
    assert "set" not in modifier
    # The token headers are still stripped before the request reaches the app.
    assert ID_TOKEN_HEADER in modifier["remove"]


def test_the_shared_policy_is_the_same_whoever_is_public():
    """Nothing about an app's switch reaches the one shared policy."""
    [policy] = [
        m
        for m in edge_post_install_manifests(CFG)
        if m["kind"] == "SecurityPolicy" and m["metadata"]["name"] == "edge-oidc"
    ]
    assert policy["spec"]["targetSelectors"][0]["matchLabels"] == {GATE_LABEL: "gated"}
    assert "passThroughAuthHeader" not in policy["spec"]["oidc"]


def test_a_client_cannot_supply_the_gateway_secret_on_a_public_route():
    [ctp] = [m for m in edge_post_install_manifests(CFG) if m["kind"] == "ClientTrafficPolicy"]
    assert DEFAULT_GATEWAY_SECRET_HEADER in ctp["spec"]["headers"]["earlyRequestHeaders"]["remove"]
