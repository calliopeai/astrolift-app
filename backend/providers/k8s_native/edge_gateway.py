"""The Envoy Gateway edge: central auth without ingress-nginx (#2055, #2117).

One Gateway per cluster, in the platform's ``astrolift-edge`` namespace,
fronts every platform-assigned app hostname. Envoy's own OIDC filter owns
the single IdP callback on the central auth host and issues a session
scoped to the parent zone, so a new app needs no callback registration,
no load balancer and no DNS record of its own.

Why this and not the #1539 nginx + oauth2-proxy edge:

* ingress-nginx reached end of maintenance in March 2026 and gets no
  security patches.
* oauth2-proxy forwards its session cookie to the upstream, so every
  tenant app's backend received a cookie valid for every other app in the
  zone. Envoy strips the session's HMAC, expiry and refresh cookies before
  the request leaves the gateway, and keeps the token cookies encrypted,
  so what an app receives cannot be replayed anywhere else.

The shape here is the one proven end to end in a local cluster (see the
PR for #2055); each non-obvious choice carries its reason inline. The
three that are easy to undo by accident:

* ONE SecurityPolicy selects every gated route by label. Envoy Gateway
  derives the session cookie names from the owning policy's UID, so a
  policy per route gives every app its own session and single sign-on
  silently stops working.
* The JWT filter runs after the OAuth2 filter (``EnvoyProxy.filterOrder``).
  By default it runs first, finds no token, and the identity headers are
  never set.
* The raw ID token travels between those two filters on an internal
  header, which every route removes before the request reaches the app.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from _sdk.cluster import BootstrapComponent

from .central_auth import cookie_scope_for, issuer_from_discovery_url

EDGE_COMPONENT_KEY = "envoy-gateway"
EDGE_CHART_VERSION = "1.9.1"

EDGE_NAMESPACE = "astrolift-edge"
GATEWAY_CLASS_NAME = "astrolift"
GATEWAY_NAME = "edge"
EDGE_PROXY_CONFIG_NAME = "edge"
EDGE_SECURITY_POLICY_NAME = "edge-oidc"
AUTH_ROUTE_NAME = "auth"

# The Service Envoy Gateway creates for the Gateway's proxies. Pinned so the
# load balancer in front of it can name it; the generated name carries a hash.
EDGE_SERVICE_NAME = "astrolift-edge"
EDGE_HTTP_PORT = 80

# The only secret the edge needs: Envoy Gateway keeps its own HMAC key for
# the session, so there is no cookie secret to carry. Built in the install
# activity, never in the recipe, which operators read over GraphQL.
EDGE_OIDC_SECRET_NAME = "astrolift-edge-oidc"

GATE_LABEL = "astrolift.dev/edge-auth"
GATE_LABEL_GATED = "gated"
ROUTE_NAMESPACE_LABEL = "astrolift.dev/namespace"

# Carries the verified ID token from the OAuth2 filter to the JWT filter.
# Stripped at the listener before any filter (so a client cannot supply
# one) and again on every route (so the app never sees it).
ID_TOKEN_HEADER = "X-Astrolift-Id-Token"

USER_HEADER = "X-Auth-Request-User"
EMAIL_HEADER = "X-Auth-Request-Email"
DEFAULT_GATEWAY_SECRET_HEADER = "X-Astrolift-Gateway-Secret"

ACCESS_TOKEN_COOKIE = "AccessToken-astrolift"
ID_TOKEN_COOKIE = "IdToken-astrolift"

CALLBACK_PATH = "/oauth2/callback"
LOGOUT_PATH = "/oauth2/logout"

_MANAGED_LABELS = {"astrolift.io/managed-by": "platform"}


def edge_configured(oidc_auth_config: dict[str, Any] | None) -> bool:
    """The cluster carries everything the edge's OIDC policy renders from.

    The same three keys the nginx gate requires (``oidc_auth_for_cluster``),
    so a cluster is gated or not by one definition whichever edge it runs.
    """
    config = oidc_auth_config or {}
    return all(str(config.get(k) or "").strip() for k in ("discovery_url", "client_id", "auth_proxy_host"))


def edge_zone(oidc_auth_config: dict[str, Any] | None) -> str:
    """The zone the session cookie covers: the auth host's parent, no dot."""
    host = str((oidc_auth_config or {}).get("auth_proxy_host") or "")
    return cookie_scope_for(host).lstrip(".")


def _manifest(api_version: str, kind: str, name: str, namespace: str | None = None, **body: Any) -> dict:
    metadata: dict[str, Any] = {"name": name, "labels": dict(_MANAGED_LABELS)}
    if namespace:
        metadata["namespace"] = namespace
    return {"apiVersion": api_version, "kind": kind, "metadata": metadata, **body}


def edge_security_policy(oidc_auth_config: dict[str, Any]) -> dict[str, Any]:
    """The one OIDC + JWT policy every gated route shares."""
    config = oidc_auth_config
    issuer = issuer_from_discovery_url(str(config["discovery_url"]))
    auth_host = str(config["auth_proxy_host"])
    return _manifest(
        "gateway.envoyproxy.io/v1alpha1",
        "SecurityPolicy",
        EDGE_SECURITY_POLICY_NAME,
        EDGE_NAMESPACE,
        spec={
            # A selector, not a list of routes: one owner, so one set of
            # session cookie names, so one login covers every app.
            "targetSelectors": [
                {
                    "group": "gateway.networking.k8s.io",
                    "kind": "HTTPRoute",
                    "matchLabels": {GATE_LABEL: GATE_LABEL_GATED},
                }
            ],
            "oidc": {
                "provider": {"issuer": issuer},
                "clientID": str(config["client_id"]),
                "clientSecret": {"name": EDGE_OIDC_SECRET_NAME},
                # The single callback: every app sends its logins here.
                "redirectURL": f"https://{auth_host}{CALLBACK_PATH}",
                "logoutPath": LOGOUT_PATH,
                "cookieDomain": edge_zone(config),
                # Pinned so the JWT provider below can name the cookie; the
                # other cookie names follow the policy's UID.
                "cookieNames": {"accessToken": ACCESS_TOKEN_COOKIE, "idToken": ID_TOKEN_COOKIE},
                "scopes": ["openid", "email", "profile"],
                "forwardIDToken": {"header": ID_TOKEN_HEADER},
            },
            "jwt": {
                "providers": [
                    {
                        "name": "idp",
                        "issuer": issuer,
                        # Only ID tokens minted for this client. The listener
                        # already drops a client-supplied token header; this
                        # holds even if that ever regresses.
                        "audiences": [str(config["client_id"])],
                        "remoteJWKS": {"uri": _jwks_uri(issuer, config)},
                        "extractFrom": {"headers": [{"name": ID_TOKEN_HEADER}]},
                        "claimToHeaders": [
                            {"claim": "sub", "header": USER_HEADER},
                            {"claim": "email", "header": EMAIL_HEADER},
                        ],
                    }
                ]
            },
        },
    )


def _jwks_uri(issuer: str, config: dict[str, Any]) -> str:
    """Where the issuer publishes its signing keys.

    Cognito and most providers serve ``<issuer>/.well-known/jwks.json``;
    an operator on a provider that does not sets ``jwks_uri``.
    """
    return str(config.get("jwks_uri") or "") or f"{issuer.rstrip('/')}/.well-known/jwks.json"


def edge_post_install_manifests(
    oidc_auth_config: dict[str, Any] | None,
    *,
    front: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Everything the edge needs beyond the controller's chart.

    ``front`` is the cloud's load balancer in front of the Gateway's
    Service (an ALB Ingress on EKS). The Gateway itself listens on plain
    HTTP: TLS ends at that load balancer, which already holds the zone's
    certificate.
    """
    config = oidc_auth_config or {}
    out: list[dict[str, Any]] = [
        _manifest("v1", "Namespace", EDGE_NAMESPACE),
        _manifest(
            "gateway.networking.k8s.io/v1",
            "GatewayClass",
            GATEWAY_CLASS_NAME,
            spec={"controllerName": "gateway.envoyproxy.io/gatewayclass-controller"},
        ),
        _manifest(
            "gateway.envoyproxy.io/v1alpha1",
            "EnvoyProxy",
            EDGE_PROXY_CONFIG_NAME,
            EDGE_NAMESPACE,
            spec={
                # jwt_authn after oauth2, or it runs before the ID token is
                # forwarded and the identity headers are never set.
                "filterOrder": [{"name": "envoy.filters.http.jwt_authn", "after": "envoy.filters.http.oauth2"}],
                "provider": {
                    "type": "Kubernetes",
                    "kubernetes": {
                        "envoyService": {"name": EDGE_SERVICE_NAME, "type": "ClusterIP"},
                        "envoyDeployment": {"replicas": 2},
                    },
                },
            },
        ),
        _manifest(
            "gateway.networking.k8s.io/v1",
            "Gateway",
            GATEWAY_NAME,
            EDGE_NAMESPACE,
            spec={
                "gatewayClassName": GATEWAY_CLASS_NAME,
                "infrastructure": {
                    "parametersRef": {
                        "group": "gateway.envoyproxy.io",
                        "kind": "EnvoyProxy",
                        "name": EDGE_PROXY_CONFIG_NAME,
                    }
                },
                # Routes attach only from this namespace, which only the
                # platform writes. A tenant with rights in its own namespace
                # cannot claim a hostname on the shared edge.
                "listeners": [
                    {
                        "name": "http",
                        "protocol": "HTTP",
                        "port": EDGE_HTTP_PORT,
                        "allowedRoutes": {"namespaces": {"from": "Same"}},
                    }
                ],
            },
        ),
        _manifest(
            "gateway.envoyproxy.io/v1alpha1",
            "ClientTrafficPolicy",
            GATEWAY_NAME,
            EDGE_NAMESPACE,
            spec={
                "targetRefs": [{"group": "gateway.networking.k8s.io", "kind": "Gateway", "name": GATEWAY_NAME}],
                # The load balancer in front appends the client address and
                # sets X-Forwarded-Proto; trusting that one hop is what makes
                # the post-login redirect come back as https.
                "clientIPDetection": {"xForwardedFor": {"numTrustedHops": 1}},
                # Before any filter runs, drop everything a client could use
                # to impersonate the gate. The JWT filter overwrites the
                # identity headers when the claim exists; this also covers
                # a token without the claim, and ungated routes.
                "headers": {
                    "earlyRequestHeaders": {
                        "remove": [ID_TOKEN_HEADER, USER_HEADER, EMAIL_HEADER, DEFAULT_GATEWAY_SECRET_HEADER],
                    }
                },
            },
        ),
    ]
    if edge_configured(config):
        auth_host = str(config["auth_proxy_host"])
        out.extend(
            [
                edge_security_policy(config),
                # The auth host exists so the callback has a route to run
                # on. Past the login it has nothing to serve.
                _manifest(
                    "gateway.envoyproxy.io/v1alpha1",
                    "HTTPRouteFilter",
                    AUTH_ROUTE_NAME,
                    EDGE_NAMESPACE,
                    spec={"directResponse": {"statusCode": 200, "body": {"type": "Inline", "inline": "ok"}}},
                ),
                _route(
                    AUTH_ROUTE_NAME,
                    [auth_host],
                    gated=True,
                    rules=[
                        {
                            "filters": [
                                {
                                    "type": "ExtensionRef",
                                    "extensionRef": {
                                        "group": "gateway.envoyproxy.io",
                                        "kind": "HTTPRouteFilter",
                                        "name": AUTH_ROUTE_NAME,
                                    },
                                }
                            ]
                        }
                    ],
                ),
            ]
        )
    out.extend(front or [])
    return out


def alb_front(oidc_auth_config: dict[str, Any] | None, *, namespace: str) -> list[dict[str, Any]]:
    """An ALB in front of the Gateway's Service, for EKS.

    One wildcard host covers every app and the auth host, so the AWS Load
    Balancer Controller finds the zone's ACM certificate by that host and
    external-dns writes one wildcard record. An app that still has its own
    ALB Ingress keeps its own, more specific record until it moves.

    The health check probes the listener with no route for the target's
    address, which Envoy answers 404: any status short of a 5xx means the
    proxy is up. A 500 is what a broken SecurityPolicy renders on every
    gated route, and that should fail the check.
    """
    zone = edge_zone(oidc_auth_config)
    if not zone:
        return []
    host = f"*.{zone}"
    ingress = _manifest(
        "networking.k8s.io/v1",
        "Ingress",
        EDGE_SERVICE_NAME,
        namespace,
        spec={
            "ingressClassName": "alb",
            "tls": [{"hosts": [host]}],
            "rules": [
                {
                    "host": host,
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "pathType": "Prefix",
                                "backend": {"service": {"name": EDGE_SERVICE_NAME, "port": {"number": EDGE_HTTP_PORT}}},
                            }
                        ]
                    },
                }
            ],
        },
    )
    ingress["metadata"]["annotations"] = {
        "alb.ingress.kubernetes.io/scheme": "internet-facing",
        "alb.ingress.kubernetes.io/target-type": "ip",
        "alb.ingress.kubernetes.io/listen-ports": '[{"HTTP": 80}, {"HTTPS": 443}]',
        "alb.ingress.kubernetes.io/ssl-redirect": "443",
        "alb.ingress.kubernetes.io/healthcheck-path": "/",
        "alb.ingress.kubernetes.io/success-codes": "200-499",
    }
    return [ingress]


def edge_component(
    oidc_auth_config: dict[str, Any] | None,
    *,
    ingress_class: str,
    front: list[dict[str, Any]] | None = None,
) -> BootstrapComponent:
    """The ``envoy-gateway`` bootstrap component.

    Enabled by default only on a cluster that asked for it
    (``ingress_class == "envoy"``) or that carries a complete OIDC config
    for it to gate with, so an ALB-only cluster's recipe does not change.
    """
    configured = edge_configured(oidc_auth_config)
    return BootstrapComponent(
        key=EDGE_COMPONENT_KEY,
        title="Envoy Gateway (edge + central auth)",
        default_enabled=ingress_class == "envoy",
        rationale=(
            "One Gateway API edge for every app hostname, with the central auth "
            "host's single OIDC callback built into Envoy. A new app needs no "
            "callback registration, load balancer or DNS record, and the session "
            "cookie never reaches an app backend in a replayable form. Replaces "
            "the ingress-nginx edge, which is past end of maintenance."
            + ("" if configured else " Set oidcAuthConfig on the cluster to turn on the gate.")
        ),
        helm_values={},
        requires=[
            "oidc_auth_config set on cluster (discovery_url, client_id, client_secret, auth_proxy_host)",
        ],
        options=[],
        chart_name="gateway-helm",
        chart_repo_url="oci://docker.io/envoyproxy",
        chart_repo_type="oci",
        chart_version=EDGE_CHART_VERSION,
        post_install_manifests=edge_post_install_manifests(oidc_auth_config, front=front),
    )


def edge_oidc_secret_manifest(oidc_auth_config: dict[str, Any] | None) -> dict[str, Any] | None:
    """The client secret Envoy exchanges the login code with, or ``None``."""
    secret = str((oidc_auth_config or {}).get("client_secret") or "")
    if not secret:
        return None
    manifest = _manifest("v1", "Secret", EDGE_OIDC_SECRET_NAME, EDGE_NAMESPACE)
    manifest["type"] = "Opaque"
    manifest["data"] = {"client-secret": base64.b64encode(secret.encode("utf-8")).decode("ascii")}
    return manifest


# ---- per-app routes --------------------------------------------------------


def route_name(namespace: str, workload: str) -> str:
    """One route per environment workload, unique across the whole edge.

    The namespace is unique per environment (#1922), so it disambiguates
    two apps that share a workload name. Hashed when long: route names
    are object names, capped at 253, but labels and some tooling cap at 63.
    """
    base = f"{namespace}-{workload}"
    if len(base) <= 63:
        return base
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:10]
    return f"{base[:52].rstrip('-')}-{digest}"


def _route(
    name: str,
    hostnames: list[str],
    *,
    gated: bool,
    rules: list[dict[str, Any]],
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    route = _manifest(
        "gateway.networking.k8s.io/v1",
        "HTTPRoute",
        name,
        EDGE_NAMESPACE,
        spec={"parentRefs": [{"name": GATEWAY_NAME}], "hostnames": list(hostnames), "rules": rules},
    )
    route["metadata"]["labels"].update(labels or {})
    if gated:
        route["metadata"]["labels"][GATE_LABEL] = GATE_LABEL_GATED
    return route


def render_app_routes(
    *,
    app_slug: str,
    namespace: str,
    workloads: dict[str, tuple[list[str], int]],
    gated: bool,
    paused: bool,
    gateway_secret: str = "",
    gateway_secret_header: str = "",
) -> list[dict[str, Any]]:
    """The HTTPRoutes and ReferenceGrant that put one environment on the edge.

    ``workloads`` maps a workload's Service name to its hostnames and port.
    Routes live in the edge namespace, where only the platform writes; the
    ReferenceGrant in the app's namespace is what lets them reach its
    Services, and it names each Service rather than the whole namespace.
    """
    labels = {
        "astrolift.dev/app": app_slug,
        ROUTE_NAMESPACE_LABEL: namespace,
        "astrolift.dev/managed-subdomain": "true",
    }
    header_set: list[dict[str, str]] = []
    if gated and gateway_secret:
        # Set, not add: whatever a client sent under this name is replaced,
        # and the listener already dropped the default name before the gate.
        header_set.append({"name": gateway_secret_header or DEFAULT_GATEWAY_SECRET_HEADER, "value": gateway_secret})
    out: list[dict[str, Any]] = []
    for workload, (hostnames, port) in sorted(workloads.items()):
        if paused:
            filter_name = route_name(namespace, workload)
            out.append(
                _manifest(
                    "gateway.envoyproxy.io/v1alpha1",
                    "HTTPRouteFilter",
                    filter_name,
                    EDGE_NAMESPACE,
                    spec={
                        "directResponse": {
                            "statusCode": 503,
                            "body": {"type": "Inline", "inline": "Astrolift: app is paused"},
                        }
                    },
                )
            )
            out[-1]["metadata"]["labels"].update(labels)
            rule: dict[str, Any] = {
                "filters": [
                    {
                        "type": "ExtensionRef",
                        "extensionRef": {
                            "group": "gateway.envoyproxy.io",
                            "kind": "HTTPRouteFilter",
                            "name": filter_name,
                        },
                    }
                ]
            }
        else:
            modifier: dict[str, Any] = {"remove": [ID_TOKEN_HEADER]}
            if header_set:
                modifier["set"] = header_set
            rule = {
                "backendRefs": [{"name": workload, "namespace": namespace, "port": port}],
                "filters": [{"type": "RequestHeaderModifier", "requestHeaderModifier": modifier}],
            }
        out.append(_route(route_name(namespace, workload), hostnames, gated=gated, rules=[rule], labels=labels))
    if workloads and not paused:
        grant = _manifest(
            "gateway.networking.k8s.io/v1beta1",
            "ReferenceGrant",
            "astrolift-edge",
            namespace,
            spec={
                "from": [{"group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "namespace": EDGE_NAMESPACE}],
                "to": [{"group": "", "kind": "Service", "name": w} for w in sorted(workloads)],
            },
        )
        grant["metadata"]["labels"]["astrolift.dev/app"] = app_slug
        out.append(grant)
    return out


__all__ = [
    "EDGE_COMPONENT_KEY",
    "EDGE_NAMESPACE",
    "EDGE_OIDC_SECRET_NAME",
    "EDGE_SERVICE_NAME",
    "GATE_LABEL",
    "ROUTE_NAMESPACE_LABEL",
    "alb_front",
    "edge_component",
    "edge_configured",
    "edge_oidc_secret_manifest",
    "edge_post_install_manifests",
    "edge_zone",
    "render_app_routes",
    "route_name",
]
