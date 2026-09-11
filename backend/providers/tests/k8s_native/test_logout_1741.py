"""Logout is fixed browser navigation, separate from the login redirect policy."""

import pytest

from k8s_native.central_auth import central_auth_component
from k8s_native.ingress import OIDCAuthConfig, nginx_auth_annotations
from k8s_native.logout import configured_logout_url

SNIPPET = "nginx.ingress.kubernetes.io/configuration-snippet"
CONFIG = {
    "discovery_url": "https://issuer.example.com",
    "client_id": "central-client",
    "auth_proxy_host": "auth.apps.example.com",
    "logout_url": "https://idp.example.com/logout?client_id=central-client&logout_uri=https%3A%2F%2Fauth.apps.example.com%2Fauth%2Flogged-out",
}


def test_recipe_routes_through_proxy_before_the_provider_without_widening_allowlist():
    values = central_auth_component(CONFIG).helm_values
    snippet = values["ingress"]["annotations"][SNIPPET]
    assert "if ($uri = /auth/logout)" in snippet
    assert "/oauth2/sign_out?rd=%2Fauth%2Fend-session" in snippet
    assert "if ($uri = /auth/end-session)" in snippet
    assert CONFIG["logout_url"] in snippet
    assert "if ($uri = /auth/logged-out)" in snippet
    assert 'return 200 "You have signed out."' in snippet
    assert snippet.count('Cache-Control "no-store"') == 3
    assert "$arg_" not in snippet and "$args" not in snippet
    assert values["extraArgs"]["whitelist-domain"] == ".apps.example.com"


def test_existing_cluster_keeps_its_recipe_without_logout():
    config = {k: v for k, v in CONFIG.items() if k != "logout_url"}
    assert SNIPPET not in central_auth_component(config).helm_values["ingress"]["annotations"]
    assert central_auth_component(None).default_enabled is False


def test_proxy_overrides_keep_their_existing_meaning():
    config = {**CONFIG, "proxy_extra_args": {"whitelist-domain": "app.apps.example.com"}}
    assert central_auth_component(config).helm_values["extraArgs"]["whitelist-domain"] == "app.apps.example.com"


def test_app_logout_shares_the_gateway_fence_and_preserves_app_headers():
    auth = OIDCAuthConfig(auth_proxy_host=CONFIG["auth_proxy_host"], logout_enabled=True, gateway_secret="secret")
    foreign = 'proxy_set_header X-App-Header "app-value";\n'
    edge = {"identity_headers": [["email", "X-App-Email"]]}
    first = nginx_auth_annotations(auth, existing_snippet=foreign, edge=edge)[SNIPPET]
    assert first.startswith(foreign)
    assert 'return 302 "https://auth.apps.example.com/auth/logout"' in first
    assert 'proxy_set_header X-Astrolift-Gateway-Secret "secret"' in first
    assert "proxy_set_header X-App-Email" in first
    assert nginx_auth_annotations(auth, existing_snippet=first, edge=edge)[SNIPPET] == first


@pytest.mark.parametrize(
    "value",
    [
        1,
        [],
        {},
        True,
        "http://idp.example/logout",
        "//idp.example/logout",
        "/logout",
        "https://user:secret@idp.example/logout",
        "https://idp.example/logout#fragment",
        "https://idp.example:0/logout",
        "https://idp.example:65536/logout",
        "https://idp.example/logout\nInjected: value",
        'https://idp.example/";return',
        "https://idp.example/$host",
        "https://idp.example/{id_token}",
        "https://idp.example/\\logout",
        "https://idp..example/logout",
        "https://idp.example/café",
    ],
)
def test_unsafe_or_malformed_url_is_rejected_without_echo(value):
    with pytest.raises(ValueError, match="logout_url") as caught:
        configured_logout_url({**CONFIG, "logout_url": value})
    assert "secret@" not in str(caught.value)
    assert "Injected" not in str(caught.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("auth_proxy_host", "https://auth.apps.example.com"),
        ("auth_proxy_host", "auth.apps.example.com/path"),
        ("auth_proxy_host", 'auth.apps.example.com";'),
        ("client_id", ""),
        ("discovery_url", ""),
    ],
)
def test_logout_requires_complete_safe_central_host_config(field, value):
    with pytest.raises(ValueError):
        configured_logout_url({**CONFIG, field: value})
