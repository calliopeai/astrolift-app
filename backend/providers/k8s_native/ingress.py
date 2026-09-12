"""k8s-native IngressDriver — multi-variant (#49 + #8).

Supports the common in-cluster ingress controllers:
- nginx-ingress (host-based + path-based routing)
- Gateway API (HTTPRoute + Gateway, the upstream "next" model)
- Traefik (router CRDs)
- Kong (Ingress + KongPlugin CRDs)
- Istio Gateway / VirtualService

The driver dispatches per ``variant`` to the appropriate render
path. Mutations (update/delete) delegate to ClusterDriver.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.ingress import IngressDriver, Manifest

from .logout import LOGOUT_PATH, redirect_snippet

SUPPORTED_VARIANTS = (
    "nginx_ingress",
    "gateway_api",
    "traefik",
    "kong",
    "istio_gateway",
)


@dataclass(frozen=True)
class OIDCAuthConfig:
    """oauth2-proxy OIDC auth config for nginx-style ingress auth_request.

    When set on a K8sIngressConfig, the nginx / traefik / kong ingress
    driver emits the nginx.ingress.kubernetes.io/auth-url +
    auth-signin annotations that route unauthenticated requests through
    the in-cluster oauth2-proxy (backed by Dex or an external OIDC
    provider). Dex + oauth2-proxy must be installed via the bootstrap
    recipe (``dex`` + ``oauth2-proxy`` components) before this takes
    effect.

    ``auth_proxy_host`` is the hostname at which oauth2-proxy is
    reachable. Defaults to ``auth.<cluster-domain>``; pass the actual
    hostname after the bootstrap install.
    """

    auth_proxy_host: str
    """Hostname (no scheme) at which oauth2-proxy is reachable —
    e.g. ``auth.cluster.example.com``. The driver constructs the
    auth-url / auth-signin URLs from this."""

    on_error: str = "redirect"
    """``redirect`` (default) sends users to the Dex login page on
    401; ``pass`` forwards the 401 upstream (for API clients that
    handle auth themselves)."""

    response_headers: tuple[str, ...] = (
        "X-Auth-Request-User",
        "X-Auth-Request-Email",
        "X-Auth-Request-Access-Token",
    )
    """Headers oauth2-proxy injects after a successful auth check.
    The Ingress controller passes them upstream so the app can see
    the authenticated identity."""

    gateway_secret: str = ""
    """Shared secret stamped on every request the gate lets through (#1726).

    Without it the gate is only an edge check: nothing distinguishes a request
    that came through the auth host from anything else that can reach the app's
    Service port in-cluster. A careful app refuses everyone on that basis; a
    careless one trusts identity headers any pod could forge.

    Empty omits the header rather than stamping a blank one, which would read
    as "the gate vouched for this" to an app testing presence rather than
    value."""

    gateway_secret_header: str = "X-Astrolift-Gateway-Secret"
    """Header the shared secret is stamped on. Apps point their own
    proxy-secret setting at this name."""

    logout_enabled: bool = False
    """The central host has a configured browser logout flow (#1741)."""


# The three annotation keys that route an nginx-family Ingress through
# the central auth host. ``core.ingress_reconcile`` patches exactly
# these keys, so this tuple is the canonical set for the render path
# and the in-place patch path alike -- they must not drift.
NGINX_AUTH_ANNOTATION_KEYS = (
    "nginx.ingress.kubernetes.io/auth-url",
    "nginx.ingress.kubernetes.io/auth-signin",
    "nginx.ingress.kubernetes.io/auth-response-headers",
    # The gateway-secret snippet is part of the gate, so the live patcher owns
    # it too: a reconcile that refreshed the auth-* keys but left a stale
    # snippet would have the app comparing against a secret the gate no longer
    # sends, and every request would fail closed.
    "nginx.ingress.kubernetes.io/configuration-snippet",
)


# The platform's contribution to ``configuration-snippet`` is fenced by
# these markers so it can be replaced, or removed, without touching
# anything else in the same annotation (#1726).
#
# The key is not the platform's to own outright. An app can have its own
# reason to need snippet content -- one live app derives per-request
# identity headers its backend requires -- and the first shape of this
# feature rendered the whole key, so a reconcile deleted that content and
# the app started refusing every request. Fencing makes the platform's
# block identifiable, so composing is possible and clobbering is not.
PLATFORM_SNIPPET_BEGIN = "# BEGIN astrolift managed gateway headers"
PLATFORM_SNIPPET_END = "# END astrolift managed gateway headers"


# Which oauth2-proxy response header each declarable identity arrives on.
# Mirrors ``astrolift_manifest.parser._EDGE_IDENTITIES``; the manifest
# parser refuses anything outside it, so a name reaching here is known.
_EDGE_IDENTITY_SOURCES = {
    "user": "X-Auth-Request-User",
    "email": "X-Auth-Request-Email",
    "access_token": "X-Auth-Request-Access-Token",
}


def _nginx_variable(header: str) -> str:
    """The ``$upstream_http_*`` variable an auth-response header lands on."""
    return "$upstream_http_" + header.lower().replace("-", "_")


def platform_gateway_snippet(auth: OIDCAuthConfig, *, edge: dict | None = None) -> str:
    """The fenced snippet block the platform contributes, or ``""``.

    Stamps proof-of-passage on the proxied request. ``proxy_set_header``
    runs after ``auth_request``, so this lands on requests the gate
    admitted and on nothing else -- a request that never passed the gate
    never reaches this block.

    The header is set unconditionally rather than copied from the client,
    which is what makes it unforgeable from outside: whatever a caller
    sends under this name is overwritten before the app sees it.

    ``edge`` is the app's ``[edge]`` manifest block (#1733), already
    normalized: an app whose backend reads the gate's identity under names
    it chose declares the mapping and the platform renders it here, inside
    the same fence, instead of the operator applying it to the live
    Ingress by hand where nothing recreates or records it.
    """

    edge = edge or {}
    secret_header = str(edge.get("gateway_secret_header") or "") or auth.gateway_secret_header
    lines: list[str] = []
    if auth.logout_enabled:
        lines.append(redirect_snippet(LOGOUT_PATH, f"https://{auth.auth_proxy_host}{LOGOUT_PATH}"))
    if auth.gateway_secret:
        lines.append(f'proxy_set_header {secret_header} "{auth.gateway_secret}";')
    for pair in edge.get("identity_headers") or []:
        try:
            identity, header = pair
        except (TypeError, ValueError):
            continue
        source = _EDGE_IDENTITY_SOURCES.get(str(identity))
        if not source or not header:
            continue
        # A local variable per identity, named for the target header, so
        # two apps on one controller cannot collide and re-rendering is
        # byte-stable.
        var = "$astrolift_edge_" + str(identity)
        lines.append(f"auth_request_set {var} {_nginx_variable(source)};")
        lines.append(f"proxy_set_header {header} {var};")
    if not lines:
        return ""
    body = "\n".join(lines)
    return f"{PLATFORM_SNIPPET_BEGIN}\n{body}\n{PLATFORM_SNIPPET_END}"


def strip_platform_snippet(existing: str) -> str:
    """``existing`` with any previously-rendered platform block removed."""

    text = existing or ""
    while True:
        start = text.find(PLATFORM_SNIPPET_BEGIN)
        if start == -1:
            return text.strip("\n")
        end = text.find(PLATFORM_SNIPPET_END, start)
        if end == -1:
            # An unterminated marker means someone edited inside the
            # fence. Drop from the marker to the end rather than guess
            # where the platform's content stopped.
            return text[:start].strip("\n")
        text = text[:start] + text[end + len(PLATFORM_SNIPPET_END) :]


def compose_configuration_snippet(existing: str, block: str) -> str | None:
    """Merge the platform's ``block`` into ``existing``, keeping the rest.

    Returns ``None`` when nothing is left to write, which the callers
    turn into a delete of the annotation -- so removing the gateway
    secret from a cluster whose apps contribute nothing of their own
    still clears the key, while an app that does contribute keeps it.
    """

    foreign = strip_platform_snippet(existing)
    parts = [p for p in (foreign, block) if p]
    return "\n".join(parts) + "\n" if parts else None


def nginx_auth_annotations(
    auth: OIDCAuthConfig,
    *,
    existing_snippet: str = "",
    edge: dict | None = None,
) -> dict[str, str]:
    """Annotations pointing an nginx-family Ingress at the central auth
    host.

    Split out of ``_render_nginx_style`` so every producer of a gated
    Ingress emits byte-identical values: this driver, the workflow's
    own managed-subdomain renderer
    (``astrolift_workflows.activities.app_lifecycle``), and the live
    patcher (``core.ingress_reconcile``). Three call sites hand-rolling
    the gate is how an app ends up authenticated on one render path and
    public on another.
    """
    annotations = {
        "nginx.ingress.kubernetes.io/auth-url": f"https://{auth.auth_proxy_host}/oauth2/auth",
        # rd must be the FULL app URL: the auth host lives on its own
        # hostname, so a path-only rd lands the user on the auth host
        # after login instead of back on the app. oauth2-proxy's
        # whitelist-domain gates which hosts the full-URL redirect
        # may target.
        "nginx.ingress.kubernetes.io/auth-signin": (
            f"https://{auth.auth_proxy_host}/oauth2/start?rd=https://$host$escaped_request_uri"
        ),
        "nginx.ingress.kubernetes.io/auth-response-headers": ",".join(auth.response_headers),
    }
    snippet = compose_configuration_snippet(
        existing_snippet,
        platform_gateway_snippet(auth, edge=edge),
    )
    if snippet is not None:
        annotations["nginx.ingress.kubernetes.io/configuration-snippet"] = snippet
    return annotations


@dataclass(frozen=True)
class K8sIngressConfig:
    variant: str = "nginx_ingress"
    """One of SUPPORTED_VARIANTS. Determines which annotations +
    CRDs the driver renders."""

    ingress_class_name: str = "nginx"
    """For nginx_ingress + traefik. Matches the ingress controller
    deployed on the cluster."""

    gateway_class_name: str = "istio"
    """For gateway_api + istio_gateway."""

    cert_manager_issuer: str = "letsencrypt-prod"
    """ClusterIssuer name for cert-manager-driven cert provisioning."""

    oidc_auth: OIDCAuthConfig | None = None
    """When set, nginx / traefik / kong Ingresses carry the
    nginx.ingress.kubernetes.io/auth-url + auth-signin annotations
    that gate access through the in-cluster oauth2-proxy. Null = no
    auth gate (public access). Gateway API + Istio variants ignore
    this field — those controllers use a different extension model for
    auth (ExtensionRef / EnvoyFilter) not yet wired up."""

    cluster_driver: Any | None = None


class K8sIngressDriver(IngressDriver):
    def __init__(self, *, config: K8sIngressConfig) -> None:
        if config.variant not in SUPPORTED_VARIANTS:
            raise ValueError(
                f"variant {config.variant!r} not in {SUPPORTED_VARIANTS}",
            )
        self._config = config

    @driver_op(cloud="k8s_native", driver="ingress")
    def render_ingress(
        self,
        app: str,
        workload: str,
        hostnames: list[str],
        tls_strategy: str,
        port: int = 80,
    ) -> list[Manifest]:
        if self._config.variant == "nginx_ingress":
            return self._render_nginx(
                app=app,
                workload=workload,
                hostnames=hostnames,
                tls_strategy=tls_strategy,
                port=port,
            )
        if self._config.variant == "traefik":
            return self._render_nginx_style(
                app=app,
                workload=workload,
                hostnames=hostnames,
                tls_strategy=tls_strategy,
                ingress_class="traefik",
                port=port,
            )
        if self._config.variant == "gateway_api":
            return self._render_gateway_api(
                app=app,
                workload=workload,
                hostnames=hostnames,
                tls_strategy=tls_strategy,
            )
        if self._config.variant == "kong":
            return self._render_nginx_style(
                app=app,
                workload=workload,
                hostnames=hostnames,
                tls_strategy=tls_strategy,
                ingress_class="kong",
                port=port,
            )
        if self._config.variant == "istio_gateway":
            return self._render_istio(
                app=app,
                workload=workload,
                hostnames=hostnames,
                tls_strategy=tls_strategy,
                port=port,
            )
        raise ValueError(f"unhandled variant {self._config.variant!r}")

    @driver_op(cloud="k8s_native", driver="ingress")
    def update_ingress_host(
        self,
        cluster: str,
        namespace: str,
        app: str,
        workload: str,
        new_hostname: str,
    ) -> None:
        if self._config.cluster_driver is None:
            raise RuntimeError(
                "update_ingress_host requires cluster_driver",
            )
        manifests = self.render_ingress(
            app=app,
            workload=workload,
            hostnames=[new_hostname],
            tls_strategy="letsencrypt",
        )
        result = self._config.cluster_driver.apply_manifests(
            cluster,
            namespace,
            manifests,
        )
        if not result.ok:
            raise RuntimeError(
                f"failed to update ingress: {result.summary()}",
            )

    @driver_op(cloud="k8s_native", driver="ingress")
    def delete_ingress(
        self,
        cluster: str,
        namespace: str,
        app: str,
        workload: str,
    ) -> None:
        if self._config.cluster_driver is None:
            raise RuntimeError(
                "delete_ingress requires cluster_driver",
            )
        # Delete via best-effort manifest stubs covering all
        # variants the driver could've rendered. ClusterDriver
        # treats not-found as "not_found" not error.
        stubs = [
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "Ingress",
                "metadata": {
                    "name": f"{app}-{workload}",
                    "namespace": namespace,
                },
            },
            {
                "apiVersion": "gateway.networking.k8s.io/v1",
                "kind": "HTTPRoute",
                "metadata": {
                    "name": f"{app}-{workload}",
                    "namespace": namespace,
                },
            },
            {
                "apiVersion": "networking.istio.io/v1",
                "kind": "VirtualService",
                "metadata": {
                    "name": f"{app}-{workload}",
                    "namespace": namespace,
                },
            },
        ]
        result = self._config.cluster_driver.delete_manifests(
            cluster,
            namespace,
            stubs,
        )
        if result.errors:
            raise RuntimeError(
                f"failed to delete ingress: {result.summary()}",
            )

    # ---- per-variant renderers -----------------------------------

    def _render_nginx(
        self,
        *,
        app: str,
        workload: str,
        hostnames: list[str],
        tls_strategy: str,
        port: int = 80,
    ) -> list[Manifest]:
        return self._render_nginx_style(
            app=app,
            workload=workload,
            hostnames=hostnames,
            tls_strategy=tls_strategy,
            ingress_class=self._config.ingress_class_name,
            port=port,
        )

    def _render_nginx_style(
        self,
        *,
        app: str,
        workload: str,
        hostnames: list[str],
        tls_strategy: str,
        ingress_class: str,
        port: int = 80,
    ) -> list[Manifest]:
        """Generic Ingress shape works for nginx, traefik, kong."""
        annotations: dict[str, str] = {}
        if tls_strategy == "letsencrypt":
            annotations["cert-manager.io/cluster-issuer"] = self._config.cert_manager_issuer
        if self._config.oidc_auth is not None:
            annotations.update(nginx_auth_annotations(self._config.oidc_auth))

        rules = [
            {
                "host": h,
                "http": {
                    "paths": [
                        {
                            "path": "/",
                            "pathType": "Prefix",
                            "backend": {
                                "service": {
                                    "name": workload,
                                    "port": {"number": port},
                                },
                            },
                        }
                    ],
                },
            }
            for h in hostnames
        ]

        ingress: Manifest = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": f"{app}-{workload}",
                "labels": _labels(app=app, workload=workload),
                "annotations": annotations,
            },
            "spec": {
                "ingressClassName": ingress_class,
                "rules": rules,
            },
        }
        if tls_strategy in ("letsencrypt", "provided"):
            ingress["spec"]["tls"] = [
                {
                    "hosts": list(hostnames),
                    "secretName": f"{app}-{workload}-tls",
                }
            ]
        return [ingress]

    def _render_gateway_api(
        self,
        *,
        app: str,
        workload: str,
        hostnames: list[str],
        tls_strategy: str,
    ) -> list[Manifest]:
        """Gateway API HTTPRoute. Cluster owns the Gateway separately."""
        route: Manifest = {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "HTTPRoute",
            "metadata": {
                "name": f"{app}-{workload}",
                "labels": _labels(app=app, workload=workload),
            },
            "spec": {
                "parentRefs": [
                    {
                        "name": self._config.gateway_class_name,
                        "namespace": "gateway-system",
                    }
                ],
                "hostnames": list(hostnames),
                "rules": [
                    {
                        "matches": [{"path": {"type": "PathPrefix", "value": "/"}}],
                        "backendRefs": [
                            {
                                "name": workload,
                                "port": 80,
                            }
                        ],
                    }
                ],
            },
        }
        return [route]

    def _render_istio(
        self,
        *,
        app: str,
        workload: str,
        hostnames: list[str],
        tls_strategy: str,
        port: int = 80,
    ) -> list[Manifest]:
        """Istio Gateway + VirtualService."""
        gateway: Manifest = {
            "apiVersion": "networking.istio.io/v1",
            "kind": "Gateway",
            "metadata": {
                "name": f"{app}-{workload}-gw",
                "labels": _labels(app=app, workload=workload),
            },
            "spec": {
                "selector": {"istio": "ingressgateway"},
                "servers": [
                    {
                        "port": {
                            "number": 443 if tls_strategy != "off" else 80,
                            "protocol": "HTTPS" if tls_strategy != "off" else "HTTP",
                            "name": "https" if tls_strategy != "off" else "http",
                        },
                        "hosts": list(hostnames),
                        "tls": (
                            {
                                "mode": "SIMPLE",
                                "credentialName": f"{app}-{workload}-tls",
                            }
                            if tls_strategy != "off"
                            else None
                        ),
                    }
                ],
            },
        }
        # Drop None TLS field
        if gateway["spec"]["servers"][0]["tls"] is None:
            del gateway["spec"]["servers"][0]["tls"]

        virtualservice: Manifest = {
            "apiVersion": "networking.istio.io/v1",
            "kind": "VirtualService",
            "metadata": {
                "name": f"{app}-{workload}",
                "labels": _labels(app=app, workload=workload),
            },
            "spec": {
                "hosts": list(hostnames),
                "gateways": [f"{app}-{workload}-gw"],
                "http": [
                    {
                        "route": [
                            {
                                "destination": {
                                    "host": workload,
                                    "port": {"number": port},
                                },
                            }
                        ],
                    }
                ],
            },
        }
        return [gateway, virtualservice]


def _labels(*, app: str, workload: str) -> dict[str, str]:
    return {
        "astrolift.io/app": app,
        "astrolift.io/workload": workload,
        "astrolift.io/managed-by": "platform",
    }
