"""A custom hostname has its own host-bound session and exact route target."""

import copy

from k8s_native.edge_gateway import (
    GATE_LABEL,
    alb_front,
    edge_post_install_manifests,
    edge_security_policy,
    render_custom_domain_routes,
)

CONFIG = {
    "discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc",
    "client_id": "client",
    "auth_proxy_host": "auth.apps.example.net",
    "custom_domain_alb_group": "verified-edge",
    "gateway_secret": "gateway-proof",
}


def render(host="customer.example", *, gated=True, paused=False):
    return render_custom_domain_routes(
        app_slug="app",
        namespace="org-app",
        hostname=host,
        service="web",
        port=8080,
        gated=gated,
        paused=paused,
        config=CONFIG,
        access={"groups": ["staff"], "users": []},
        certificate_arn="arn:aws:acm:us-west-2:123:certificate/custom",
        alb_group="verified-edge",
        platform_namespace="astrolift-system",
    )


def one(items, kind):
    return next(item for item in items if item["kind"] == kind)


def test_custom_session_cannot_accept_central_or_sibling_session_cookies():
    central = copy.deepcopy(edge_security_policy(CONFIG))
    first = render()
    second = render("other.customer.example")
    policy = one(first, "SecurityPolicy")
    route = one(first, "HTTPRoute")
    assert policy["spec"]["targetRefs"][0]["name"] == route["metadata"]["name"]
    assert "targetSelectors" not in policy["spec"]
    oidc = policy["spec"]["oidc"]
    assert oidc["redirectURL"] == "https://customer.example/oauth2/callback"
    assert "cookieDomain" not in oidc
    assert oidc["cookieNames"] != central["spec"]["oidc"]["cookieNames"]
    assert oidc["cookieNames"] != one(second, "SecurityPolicy")["spec"]["oidc"]["cookieNames"]
    assert route["metadata"]["labels"][GATE_LABEL] == "custom-gated"
    assert edge_security_policy(CONFIG) == central
    assert policy["spec"]["authorization"]["defaultAction"] == "Allow"
    assert any(rule["action"] == "Deny" for rule in policy["spec"]["authorization"]["rules"])
    rule = route["spec"]["rules"][0]
    assert rule["backendRefs"] == [{"name": "web", "namespace": "org-app", "port": 8080}]
    assert rule["filters"][0]["requestHeaderModifier"]["set"][0]["value"] == "gateway-proof"


def test_default_public_route_has_no_auth_policy_or_gateway_proof():
    items = render(gated=False)
    assert not any(item["kind"] == "SecurityPolicy" for item in items)
    route = one(items, "HTTPRoute")
    assert route["metadata"]["labels"][GATE_LABEL] == "ungated"
    assert "set" not in route["spec"]["rules"][0]["filters"][0]["requestHeaderModifier"]
    front = one(items, "Ingress")
    annotations = front["metadata"]["annotations"]
    assert annotations["alb.ingress.kubernetes.io/group.name"] == "verified-edge"
    assert annotations["alb.ingress.kubernetes.io/certificate-arn"].endswith("/custom")
    assert front["spec"]["ingressClassName"] == "alb"
    assert front["spec"]["tls"][0]["hosts"] == ["customer.example"]
    assert not any(item["kind"] == "Secret" for item in items)


def test_pause_serves_503_with_matching_filter_and_no_service_grant():
    items = render(paused=True)
    route = one(items, "HTTPRoute")
    filter_ = one(items, "HTTPRouteFilter")
    assert route["spec"]["rules"][0]["filters"][0]["extensionRef"]["name"] == filter_["metadata"]["name"]
    assert filter_["spec"]["directResponse"]["statusCode"] == 503
    assert not any(item["kind"] == "ReferenceGrant" for item in items)


def test_access_refresh_updates_custom_policy_without_changing_central_session():
    items = render()
    policy = one(items, "SecurityPolicy")
    entry = {
        "hosts": [],
        "groups": ["operators"],
        "users": [],
        "custom_routes": [
            {"name": policy["metadata"]["name"], "hostname": "customer.example", "labels": policy["metadata"]["labels"]}
        ],
    }
    manifests = edge_post_install_manifests(CONFIG, access_rules=[entry])
    shared = next(
        item for item in manifests if item["kind"] == "SecurityPolicy" and item["metadata"]["name"] == "edge-oidc"
    )
    custom = next(
        item
        for item in manifests
        if item["kind"] == "SecurityPolicy" and item["metadata"]["name"] == policy["metadata"]["name"]
    )
    assert shared["spec"]["oidc"] == edge_security_policy(CONFIG)["spec"]["oidc"]
    assert "authorization" not in shared["spec"]
    assert "operators" in str(custom["spec"]["authorization"])
    assert "staff" not in str(custom["spec"]["authorization"])


def test_first_party_front_is_unchanged_until_group_explicitly_configured():
    config = {k: v for k, v in CONFIG.items() if k != "custom_domain_alb_group"}
    assert (
        "alb.ingress.kubernetes.io/group.name"
        not in alb_front(config, namespace="astrolift-system")[0]["metadata"]["annotations"]
    )
    assert (
        alb_front(CONFIG, namespace="astrolift-system")[0]["metadata"]["annotations"][
            "alb.ingress.kubernetes.io/group.name"
        ]
        == "verified-edge"
    )
