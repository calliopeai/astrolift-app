"""The Envoy Gateway edge renders the shape proven end to end (#2055).

Each assertion here pins one of the choices that silently breaks the gate
when undone: one shared policy (or single sign-on stops), the JWT filter
after OAuth2 (or no identity headers), the internal token header stripped
on the way in and on the way out (or a client can forge it, or the app
receives a bearer token), and no secret in anything the recipe returns.
"""

from __future__ import annotations

import base64
import json

from k8s_native.edge_gateway import (
    DEFAULT_GATEWAY_SECRET_HEADER,
    EDGE_NAMESPACE,
    EDGE_OIDC_SECRET_NAME,
    EDGE_SERVICE_NAME,
    GATE_LABEL,
    ID_TOKEN_HEADER,
    ROUTE_NAMESPACE_LABEL,
    alb_front,
    edge_component,
    edge_configured,
    edge_oidc_secret_manifest,
    edge_zone,
    render_app_routes,
    route_name,
)

COGNITO = {
    "discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc/.well-known/openid-configuration",
    "client_id": "central-client-id",
    "auth_proxy_host": "auth.astro.example.net",
}
CLIENT_SECRET = "client-secret-2055-never-leaves"


def _by_kind(manifests, kind):
    return [m for m in manifests if m["kind"] == kind]


def test_configured_needs_the_same_three_keys_as_the_nginx_gate():
    assert edge_configured(COGNITO)
    for key in COGNITO:
        assert not edge_configured({**COGNITO, key: ""})
    assert not edge_configured(None)


def test_zone_is_the_auth_hosts_parent():
    assert edge_zone(COGNITO) == "astro.example.net"


def test_one_policy_selects_every_gated_route_by_label():
    component = edge_component(COGNITO, ingress_class="envoy")
    [policy] = _by_kind(component.post_install_manifests, "SecurityPolicy")
    spec = policy["spec"]
    assert "targetRefs" not in spec
    assert spec["targetSelectors"] == [
        {"group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "matchLabels": {GATE_LABEL: "gated"}}
    ]
    oidc = spec["oidc"]
    assert oidc["provider"]["issuer"] == "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc"
    assert oidc["redirectURL"] == "https://auth.astro.example.net/oauth2/callback"
    assert oidc["cookieDomain"] == "astro.example.net"
    assert oidc["clientSecret"] == {"name": EDGE_OIDC_SECRET_NAME}
    assert oidc["forwardIDToken"] == {"header": ID_TOKEN_HEADER}
    [provider] = spec["jwt"]["providers"]
    assert provider["audiences"] == ["central-client-id"]
    assert provider["extractFrom"] == {"headers": [{"name": ID_TOKEN_HEADER}]}
    assert provider["remoteJWKS"]["uri"] == (
        "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc/.well-known/jwks.json"
    )
    assert {c["header"] for c in provider["claimToHeaders"]} == {"X-Auth-Request-User", "X-Auth-Request-Email"}


def test_jwks_uri_is_overridable():
    component = edge_component({**COGNITO, "jwks_uri": "https://keys.example/jwks"}, ingress_class="envoy")
    [policy] = _by_kind(component.post_install_manifests, "SecurityPolicy")
    assert policy["spec"]["jwt"]["providers"][0]["remoteJWKS"]["uri"] == "https://keys.example/jwks"


def test_jwt_runs_after_oauth2():
    component = edge_component(COGNITO, ingress_class="envoy")
    [proxy] = _by_kind(component.post_install_manifests, "EnvoyProxy")
    assert proxy["spec"]["filterOrder"] == [
        {"name": "envoy.filters.http.jwt_authn", "after": "envoy.filters.http.oauth2"}
    ]
    assert proxy["spec"]["provider"]["kubernetes"]["envoyService"]["name"] == EDGE_SERVICE_NAME


def test_listener_drops_impersonation_headers_before_any_filter():
    component = edge_component(COGNITO, ingress_class="envoy")
    [ctp] = _by_kind(component.post_install_manifests, "ClientTrafficPolicy")
    removed = set(ctp["spec"]["headers"]["earlyRequestHeaders"]["remove"])
    assert {ID_TOKEN_HEADER, "X-Auth-Request-User", "X-Auth-Request-Email", DEFAULT_GATEWAY_SECRET_HEADER} <= removed
    assert ctp["spec"]["clientIPDetection"] == {"xForwardedFor": {"numTrustedHops": 1}}


def test_routes_attach_only_from_the_platform_namespace():
    component = edge_component(COGNITO, ingress_class="envoy")
    [gateway] = _by_kind(component.post_install_manifests, "Gateway")
    assert gateway["metadata"]["namespace"] == EDGE_NAMESPACE
    assert gateway["spec"]["listeners"][0]["allowedRoutes"] == {"namespaces": {"from": "Same"}}


def test_auth_host_route_is_gated_so_the_callback_runs():
    component = edge_component(COGNITO, ingress_class="envoy")
    routes = _by_kind(component.post_install_manifests, "HTTPRoute")
    [auth] = [r for r in routes if r["spec"]["hostnames"] == ["auth.astro.example.net"]]
    assert auth["metadata"]["labels"][GATE_LABEL] == "gated"


def test_alb_only_cluster_is_not_enabled_and_renders_no_gate():
    component = edge_component(None, ingress_class="alb")
    assert component.default_enabled is False
    assert not _by_kind(component.post_install_manifests, "SecurityPolicy")
    assert not _by_kind(component.post_install_manifests, "HTTPRoute")


def test_recipe_never_carries_the_client_secret():
    component = edge_component({**COGNITO, "client_secret": CLIENT_SECRET}, ingress_class="envoy")
    rendered = json.dumps([component.helm_values, component.post_install_manifests, component.requires])
    assert CLIENT_SECRET not in rendered
    assert base64.b64encode(CLIENT_SECRET.encode()).decode() not in rendered


def test_secret_manifest_carries_only_the_client_secret():
    manifest = edge_oidc_secret_manifest({**COGNITO, "client_secret": CLIENT_SECRET, "cookie_secret": "x"})
    assert manifest["metadata"] == {
        "name": EDGE_OIDC_SECRET_NAME,
        "namespace": EDGE_NAMESPACE,
        "labels": {"astrolift.io/managed-by": "platform"},
    }
    assert manifest["data"] == {"client-secret": base64.b64encode(CLIENT_SECRET.encode()).decode()}
    assert edge_oidc_secret_manifest(COGNITO) is None


def test_alb_front_serves_the_whole_zone_on_the_acm_certificate():
    [ingress] = alb_front(COGNITO, namespace="astrolift-system")
    assert ingress["metadata"]["namespace"] == "astrolift-system"
    assert ingress["spec"]["ingressClassName"] == "alb"
    assert ingress["spec"]["tls"] == [{"hosts": ["*.astro.example.net"]}]
    [rule] = ingress["spec"]["rules"]
    assert rule["host"] == "*.astro.example.net"
    assert rule["http"]["paths"][0]["backend"]["service"]["name"] == EDGE_SERVICE_NAME
    annotations = ingress["metadata"]["annotations"]
    assert annotations["alb.ingress.kubernetes.io/target-type"] == "ip"
    assert annotations["alb.ingress.kubernetes.io/success-codes"] == "200-499"
    assert "alb.ingress.kubernetes.io/auth-type" not in annotations
    assert alb_front(None, namespace="astrolift-system") == []


def test_app_routes_live_on_the_edge_and_reach_back_by_grant():
    out = render_app_routes(
        app_slug="veruus",
        namespace="conflict-veruus",
        workloads={"web": (["veruus-demo.astro.example.net"], 8080)},
        gated=True,
        paused=False,
        gateway_secret="gw-secret",
    )
    [route] = _by_kind(out, "HTTPRoute")
    [grant] = _by_kind(out, "ReferenceGrant")
    assert route["metadata"]["namespace"] == EDGE_NAMESPACE
    assert route["metadata"]["labels"][GATE_LABEL] == "gated"
    assert route["metadata"]["labels"][ROUTE_NAMESPACE_LABEL] == "conflict-veruus"
    [rule] = route["spec"]["rules"]
    assert rule["backendRefs"] == [{"name": "web", "namespace": "conflict-veruus", "port": 8080}]
    modifier = rule["filters"][0]["requestHeaderModifier"]
    assert modifier["remove"] == [ID_TOKEN_HEADER]
    assert modifier["set"] == [{"name": DEFAULT_GATEWAY_SECRET_HEADER, "value": "gw-secret"}]
    assert grant["metadata"]["namespace"] == "conflict-veruus"
    assert grant["spec"]["to"] == [{"group": "", "kind": "Service", "name": "web"}]
    assert grant["spec"]["from"][0]["namespace"] == EDGE_NAMESPACE


def test_ungated_route_carries_no_gate_label_and_no_secret():
    out = render_app_routes(
        app_slug="pub",
        namespace="conflict-pub",
        workloads={"web": (["pub.astro.example.net"], 80)},
        gated=False,
        paused=False,
        gateway_secret="gw-secret",
    )
    [route] = _by_kind(out, "HTTPRoute")
    assert GATE_LABEL not in route["metadata"]["labels"]
    assert "set" not in route["spec"]["rules"][0]["filters"][0]["requestHeaderModifier"]


def test_custom_gateway_secret_header_is_honoured():
    out = render_app_routes(
        app_slug="a",
        namespace="ns",
        workloads={"web": (["a.z.example"], 80)},
        gated=True,
        paused=False,
        gateway_secret="s",
        gateway_secret_header="X-Proxy-Secret",
    )
    [route] = _by_kind(out, "HTTPRoute")
    assert route["spec"]["rules"][0]["filters"][0]["requestHeaderModifier"]["set"] == [
        {"name": "X-Proxy-Secret", "value": "s"}
    ]


def test_paused_app_answers_503_without_reaching_the_backend():
    out = render_app_routes(
        app_slug="a",
        namespace="ns",
        workloads={"web": (["a.z.example"], 80)},
        gated=True,
        paused=True,
    )
    [route] = _by_kind(out, "HTTPRoute")
    [flt] = _by_kind(out, "HTTPRouteFilter")
    assert "backendRefs" not in route["spec"]["rules"][0]
    assert flt["spec"]["directResponse"]["statusCode"] == 503
    assert not _by_kind(out, "ReferenceGrant")


def test_route_names_are_unique_per_environment_and_bounded():
    assert route_name("org-app", "web") == "org-app-web"
    long_ns = "o" * 40 + "-" + "a" * 40
    name = route_name(long_ns, "web")
    assert len(name) <= 63
    assert name != route_name(long_ns + "x", "web")


def test_app_alb_rule_joins_the_apps_group_and_points_at_the_edge():
    from k8s_native.edge_gateway import EDGE_FRONT_LABEL, alb_host_ingress

    ingress = alb_host_ingress(
        app_slug="veruus",
        namespace="conflict-veruus",
        hostnames=["veruus-demo.astro.example.net"],
        platform_namespace="astrolift-system",
        group_annotations={"alb.ingress.kubernetes.io/group.name": "astrolift-conflict"},
    )
    meta = ingress["metadata"]
    assert meta["namespace"] == "astrolift-system"
    assert meta["labels"][EDGE_FRONT_LABEL] == "true"
    assert meta["labels"][ROUTE_NAMESPACE_LABEL] == "conflict-veruus"
    assert "astrolift.dev/managed-subdomain" not in meta["labels"]
    assert meta["annotations"]["alb.ingress.kubernetes.io/group.name"] == "astrolift-conflict"
    assert not [k for k in meta["annotations"] if "auth" in k]
    [rule] = ingress["spec"]["rules"]
    assert rule["host"] == "veruus-demo.astro.example.net"
    assert rule["http"]["paths"][0]["backend"]["service"]["name"] == EDGE_SERVICE_NAME
    assert ingress["spec"]["tls"] == [{"hosts": ["veruus-demo.astro.example.net"]}]
