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

# Name of the Secret the oauth2-proxy release reads its credentials from.
# The recipe references it rather than carrying the values: ``helm_values``
# is returned to operators over GraphQL, and ``oidc_auth_config`` holds
# ``cookie_secret``, the key that signs the session cookie. A recipe that
# inlined it would hand a session-minting credential to every caller who
# can read the cluster.
CENTRAL_AUTH_SECRET_NAME = "astrolift-central-auth"

CENTRAL_AUTH_TLS_SECRET_NAME = "astrolift-central-auth-tls"

# Secret holding the rendered oauth2-proxy alpha config (#1726). It is a
# separate object from CENTRAL_AUTH_SECRET_NAME because its contents are a
# whole config file rather than individual keys, and because it carries the
# gateway secret -- which, like cookie_secret, must never reach helm_values:
# that dict is returned to operators over GraphQL.
CENTRAL_AUTH_ALPHA_CONFIG_SECRET_NAME = "astrolift-central-auth-alpha"

# Header the gate stamps on every authenticated request so an app can tell
# gateway traffic from anything else that can reach its Service port (#1726).
#
# The value is injected by oauth2-proxy itself, not by an Ingress annotation.
# Stamping it at the proxy keeps the secret inside the component that already
# holds the session, and means gating an app needs no nginx snippet -- which
# would otherwise require allow-snippet-annotations cluster-wide, letting any
# Ingress author inject arbitrary nginx config.
GATEWAY_SECRET_HEADER = "X-Astrolift-Gateway-Secret"


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


def alpha_config_document(gateway_secret: str) -> str:
    """The oauth2-proxy alpha config this component runs with (#1726).

    Rendered separately from ``helm_values`` and written to
    ``CENTRAL_AUTH_ALPHA_CONFIG_SECRET_NAME`` by whoever installs the
    component, because it carries the gateway secret and ``helm_values`` is
    served to operators over GraphQL.

    Two things have to be declared here rather than as flags, because
    ``--alpha-config`` takes ownership of both:

    * ``upstreams`` -- the auth host proxies nothing; it exists to answer
      ``/oauth2/auth`` for nginx's ``auth_request``. A static 202 upstream is
      the documented shape for that, and omitting the section entirely makes
      oauth2-proxy refuse to start.
    * ``injectResponseHeaders`` -- replaces ``--set-xauthrequest``. The two
      identity headers are the same ones that flag produced, so an app that
      already reads ``X-Auth-Request-*`` sees no change.

    The gateway secret is a static value rather than a claim: its point is to
    prove the request passed through this proxy, which is a property of the
    path, not of the user.
    """
    import base64

    encoded = base64.b64encode(gateway_secret.encode()).decode()
    return (
        "upstreams:\n"
        "  - id: static-202\n"
        "    path: /\n"
        "    static: true\n"
        "    staticCode: 202\n"
        "injectResponseHeaders:\n"
        "  - name: X-Auth-Request-User\n"
        "    values:\n"
        "      - claim: user\n"
        "  - name: X-Auth-Request-Email\n"
        "    values:\n"
        "      - claim: email\n"
        f"  - name: {GATEWAY_SECRET_HEADER}\n"
        "    values:\n"
        f"      - value: {encoded}\n"
    )


def central_auth_component(oidc_auth_config: dict[str, Any] | None) -> BootstrapComponent:
    """The ``oauth2-proxy`` component rendered from a cluster's config.

    Enabled once the config is complete; with nothing to render from it
    stays disabled and omits the host-derived flags rather than shipping
    placeholders an operator then has to hand-correct.
    """
    config = oidc_auth_config or {}
    auth_proxy_host = str(config.get("auth_proxy_host") or "")
    client_id = str(config.get("client_id") or "")
    issuer_url = issuer_from_discovery_url(str(config.get("discovery_url") or ""))
    cookie_scope = cookie_scope_for(auth_proxy_host)
    configured = bool(auth_proxy_host and client_id and issuer_url)

    extra_args: dict[str, str] = {
        "provider": "oidc",
        "email-domain": "*",
        # nginx's auth_request calls the proxy over the cluster network
        # with the original Host preserved; without this the proxy builds
        # its redirects from the internal address.
        "reverse-proxy": "true",
        # NOT set-xauthrequest: with --alpha-config in play, oauth2-proxy
        # rejects that flag and the identity headers are declared in
        # injectResponseHeaders instead. Setting both is a startup error, so
        # the two must not drift apart.
        "skip-provider-button": "true",
        "alpha-config": "/etc/oauth2-proxy/alpha/alpha_config.yaml",
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

    return BootstrapComponent(
        key="oauth2-proxy",
        title="oauth2-proxy (central auth host)",
        default_enabled=configured,
        rationale=(
            "The central auth host. It owns the single OIDC callback for the "
            "whole cluster and issues a session cookie scoped to the parent "
            "zone, so every nginx-class Ingress gates on it via an "
            "auth_request sub-request and a new public app needs no callback "
            "registration of its own. Rendered from the cluster's "
            "oidc_auth_config; enabled once that config is complete. Works "
            "against Dex in-cluster or any external OIDC provider."
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
                # setXauthrequest is deliberately absent: the chart renders it
                # as --set-xauthrequest, which oauth2-proxy rejects alongside
                # --alpha-config. The same two identity headers are declared in
                # alpha_config_document() instead, so the app sees no
                # difference -- but leaving both in place is a startup failure,
                # not a warning.
                "upstreamInsecureSkipVerify": False,
            },
            "extraArgs": extra_args,
            # The alpha config lives in its own Secret and is mounted at the
            # path --alpha-config points at. Referenced by name, never
            # inlined: it carries the gateway secret.
            "extraVolumes": [
                {
                    "name": "alpha-config",
                    "secret": {"secretName": CENTRAL_AUTH_ALPHA_CONFIG_SECRET_NAME},
                }
            ],
            "extraVolumeMounts": [
                {
                    "name": "alpha-config",
                    "mountPath": "/etc/oauth2-proxy/alpha",
                    "readOnly": True,
                }
            ],
            "ingress": {
                "enabled": True,
                "className": "nginx",
                "annotations": {
                    "cert-manager.io/cluster-issuer": "letsencrypt-prod",
                },
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
            (
                f"Secret {CENTRAL_AUTH_ALPHA_CONFIG_SECRET_NAME} with key alpha_config.yaml "
                f"(render via alpha_config_document(); carries the gateway secret)"
            ),
        ],
        options=[],
        chart_name="oauth2-proxy",
        chart_repo_url="https://oauth2-proxy.github.io/manifests",
        chart_repo_type="default",
        chart_version="7.7.14",
        depends_on=["dex"],
    )


__all__ = [
    "CENTRAL_AUTH_ALPHA_CONFIG_SECRET_NAME",
    "CENTRAL_AUTH_SECRET_NAME",
    "GATEWAY_SECRET_HEADER",
    "alpha_config_document",
    "CENTRAL_AUTH_TLS_SECRET_NAME",
    "central_auth_component",
    "cookie_scope_for",
    "issuer_from_discovery_url",
]
