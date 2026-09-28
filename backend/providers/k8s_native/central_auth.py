"""The central auth host bootstrap component (#1539).

One oauth2-proxy release per cluster owns the single OIDC callback and
issues a session cookie scoped to the parent zone of its own hostname.
Every nginx-family Ingress gates on it through an ``auth_request``
sub-request, so onboarding a public app needs no DNS record, no load
balancer and -- the point of the exercise -- no callback registration of
its own. Cognito matches callbacks exactly and caps them at 100 per
client, so per-app registration is both a manual step and a ceiling.

This lives outside ``k8s_native.cluster`` because every cloud driver
needs the identical component. Astrolift's tenant runtime is EKS, so a
recipe that only the vanilla-k8s driver offered would leave the auth
host unbuildable on the clusters that actually run tenant apps -- which
is how the first install ended up hand-assembling it with Flux.
``k8s_native.management`` and ``k8s_native.observability`` are shared
across the cloud drivers the same way.
"""

from __future__ import annotations

from typing import Any

from _sdk.cluster import BootstrapComponent

from .logout import central_logout_snippet

# Name of the Secret the oauth2-proxy release reads its credentials from.
# The recipe references it rather than carrying the values: ``helm_values``
# is returned to operators over GraphQL, and ``oidc_auth_config`` holds
# ``cookie_secret``, the key that signs the session cookie. A recipe that
# inlined it would hand a session-minting credential to every caller who
# can read the cluster.
CENTRAL_AUTH_SECRET_NAME = "astrolift-central-auth"

CENTRAL_AUTH_TLS_SECRET_NAME = "astrolift-central-auth-tls"


def cookie_scope_for(auth_proxy_host: str) -> str:
    """The parent zone of the auth host, dot-prefixed.

    ``auth.astro.example.com`` -> ``.astro.example.com``. This is the
    whole mechanism: a cookie scoped to the parent zone is presented on
    every app subdomain under it, so one login and one registered
    callback cover every app. Returns ``""`` for a host with no parent
    to scope to, which leaves the flag off rather than emitting a bare
    ``.com`` that browsers reject anyway.
    """
    labels = [label for label in (auth_proxy_host or "").split(".") if label]
    if len(labels) < 3:
        return ""
    return "." + ".".join(labels[1:])


def issuer_from_discovery_url(discovery_url: str) -> str:
    """Issuer URL for oauth2-proxy's ``--oidc-issuer-url``.

    The cluster row stores a discovery URL because that is what an
    operator copies out of a provider console, but oauth2-proxy wants
    the issuer and appends the well-known path itself. Passing the
    discovery URL through yields a doubled
    ``/.well-known/openid-configuration`` and a proxy that cannot start.
    """
    url = (discovery_url or "").rstrip("/")
    suffix = "/.well-known/openid-configuration"
    if url.endswith(suffix):
        url = url[: -len(suffix)]
    return url


# Flags the platform computes from the cluster's own configuration.
# Overridable like any other -- an operator who knows their provider
# better than we do should not have to hand-edit a rendered HelmRelease
# -- but each has a reason, recorded at the assignment above.
COMPUTED_PROXY_ARGS = frozenset({"oidc-issuer-url", "redirect-url", "cookie-domain", "whitelist-domain"})


def proxy_extra_args(oidc_auth_config: dict[str, Any] | None) -> dict[str, str]:
    """Operator overrides for the proxy's ``extraArgs`` (#1716).

    ``extra_args`` was a closed dict, so anything an operator had to set
    for their provider was unreachable without hand-editing the rendered
    HelmRelease. The flag that surfaced it was
    ``insecure-oidc-allow-unverified-email``: oauth2-proxy rejects a
    login whose id_token does not assert ``email_verified``, and where
    the upstream *cannot* assert it -- common for SAML federations that
    emit no equivalent, on a tenant the cluster operator does not
    control -- the proxy flag is the only lever there is.

    A general seam rather than one named boolean, because the same gap
    covers ``email-domain`` (hardcoded to ``*``, so any address the IdP
    returns is authorized) and every provider-specific flag after it.

    Keys are given without the leading dashes, matching the chart's
    ``extraArgs`` map. Values are coerced to strings: helm renders them
    onto a command line, and a YAML bool would arrive as ``True``.
    """

    raw = (oidc_auth_config or {}).get("proxy_extra_args") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        flag = str(key).strip().lstrip("-")
        if not flag or value is None:
            continue
        out[flag] = "true" if value is True else "false" if value is False else str(value)
    return out


# The classes whose Ingresses gate through oauth2-proxy's auth_request.
NGINX_FAMILY_CLASSES = frozenset({"nginx", "ingress-nginx"})


def central_auth_component(
    oidc_auth_config: dict[str, Any] | None,
    *,
    ingress_class: str = "nginx",
) -> BootstrapComponent:
    """The ``oauth2-proxy`` component rendered from a cluster's config.

    Enabled by default once the config is complete **and** the cluster is on
    an nginx-family class. The Envoy edge and the ALB gate read the same
    config but have no auth_request for the proxy to answer, so pre-checking
    it there installs a release with nothing to gate and a Secret nobody
    writes (#2121). With nothing to render from it stays disabled and omits
    the host-derived flags rather than shipping placeholders an operator
    then has to hand-correct.
    """
    config = oidc_auth_config or {}
    auth_proxy_host = str(config.get("auth_proxy_host") or "")
    client_id = str(config.get("client_id") or "")
    issuer_url = issuer_from_discovery_url(str(config.get("discovery_url") or ""))
    cookie_scope = cookie_scope_for(auth_proxy_host)
    configured = bool(auth_proxy_host and client_id and issuer_url)

    extra_args: dict[str, str] = {
        "provider": "oidc",
        # Any address the IdP returns is authorized. Narrowing this is a
        # per-cluster decision, reachable through ``proxy_extra_args``.
        "email-domain": "*",
        # nginx's auth_request calls the proxy over the cluster network
        # with the original Host preserved; without this the proxy builds
        # its redirects from the internal address.
        "reverse-proxy": "true",
        "set-xauthrequest": "true",
        "skip-provider-button": "true",
    }
    if issuer_url:
        extra_args["oidc-issuer-url"] = issuer_url
    if auth_proxy_host:
        # The SINGLE callback. Every app redirects through this one URL,
        # so the upstream IdP needs exactly one registration no matter
        # how many apps the cluster serves.
        extra_args["redirect-url"] = f"https://{auth_proxy_host}/oauth2/callback"
    if cookie_scope:
        extra_args["cookie-domain"] = cookie_scope
        # Bounds which hosts the full-URL ``rd`` may send a user back to
        # after login. The Ingress annotations pass the app's own URL, so
        # without this the proxy refuses the redirect and strands the
        # user on the auth host after a successful login.
        extra_args["whitelist-domain"] = cookie_scope

    extra_args.update(proxy_extra_args(config))

    annotations = {"cert-manager.io/cluster-issuer": "letsencrypt-prod"}
    logout_snippet = central_logout_snippet(config)
    if logout_snippet:
        annotations["nginx.ingress.kubernetes.io/configuration-snippet"] = logout_snippet

    return BootstrapComponent(
        key="oauth2-proxy",
        title="oauth2-proxy (central auth host)",
        default_enabled=configured and (ingress_class or "nginx") in NGINX_FAMILY_CLASSES,
        rationale=(
            "The central auth host. It owns the single OIDC callback for the "
            "whole cluster and issues a session cookie scoped to the parent "
            "zone, so every nginx-class Ingress gates on it via an "
            "auth_request sub-request and a new public app needs no callback "
            "registration of its own. Rendered from the cluster's "
            "oidc_auth_config; on by default once that config is complete on an "
            "nginx-class cluster. Works against Dex in-cluster or any "
            "external OIDC provider."
        ),
        helm_values={
            "config": {
                "clientID": client_id,
                # Client secret + cookie secret come from the Secret,
                # never from the recipe.
                "existingSecret": CENTRAL_AUTH_SECRET_NAME,
                "cookieSecure": True,
                "cookieName": "_astrolift_oauth2",
                "emailDomains": ["*"],
                "scope": "openid email profile groups",
                "passAccessToken": True,
                "setXauthrequest": True,
                "upstreamInsecureSkipVerify": False,
            },
            "extraArgs": extra_args,
            "ingress": {
                "enabled": True,
                "className": "nginx",
                "annotations": annotations,
                "hosts": [auth_proxy_host] if auth_proxy_host else [],
                "tls": (
                    [{"secretName": CENTRAL_AUTH_TLS_SECRET_NAME, "hosts": [auth_proxy_host]}]
                    if auth_proxy_host
                    else []
                ),
            },
            "replicaCount": 2,
        },
        requires=[
            "dex or external OIDC provider",
            "oidc_auth_config set on cluster (discovery_url, client_id, auth_proxy_host)",
            f"Secret {CENTRAL_AUTH_SECRET_NAME} (client-secret, cookie-secret) in the release namespace",
        ],
        options=[],
        chart_name="oauth2-proxy",
        chart_repo_url="https://oauth2-proxy.github.io/manifests",
        chart_repo_type="default",
        chart_version="7.7.14",
        depends_on=["dex"],
    )


__all__ = [
    "CENTRAL_AUTH_SECRET_NAME",
    "CENTRAL_AUTH_TLS_SECRET_NAME",
    "central_auth_component",
    "cookie_scope_for",
    "issuer_from_discovery_url",
]
