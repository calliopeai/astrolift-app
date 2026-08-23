"""One answer to "is this cluster gated, and with what" (#1539).

The central auth host only removes per-app Cognito registration if every
producer of an Ingress agrees about the gate. Three of them exist -- the
deploy renderer, the workflow renderer, and the live reconcile -- so the
decision is centralised in ``core.app_deploy`` and the rendered values in
``providers.k8s_native.ingress``. These tests pin the shared answer; the
per-renderer tests pin that each renderer actually asks.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.app_deploy import cognito_auth_for_cluster, oidc_auth_for_cluster
from core.ingress_reconcile import AUTH_ANNOTATION_KEYS, auth_annotation_patch
from providers.k8s_native.ingress import NGINX_AUTH_ANNOTATION_KEYS

OIDC_CONFIG = {
    "discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc",
    "client_id": "1qqhi5irhften18gbbpailuo8v",
    "cookie_secret": "signing-key",
    "auth_proxy_host": "auth.astrolift.example.net",
}
ALB_CONFIG = {
    "user_pool_arn": "arn:aws:cognito-idp:us-west-2:111122223333:userpool/us-west-2_abc",
    "user_pool_client_id": "client-abc",
    "user_pool_domain": "acme-auth",
}


def _cluster(**kwargs):
    base = {"ingress_class": "nginx", "alb_auth_config": None, "oidc_auth_config": None}
    base.update(kwargs)
    return SimpleNamespace(slug="c1", **base)


# ---- oidc_auth_for_cluster ------------------------------------------


def test_complete_oidc_config_yields_the_auth_host():
    auth = oidc_auth_for_cluster(_cluster(oidc_auth_config=OIDC_CONFIG))
    assert auth is not None
    assert auth.auth_proxy_host == "auth.astrolift.example.net"


def test_missing_oidc_config_is_not_gated():
    assert oidc_auth_for_cluster(_cluster()) is None


def test_blank_auth_proxy_host_is_not_gated():
    """Presence is not enough. The renderer used to test ``k in config``,
    so a blank host rendered ``https:///oauth2/auth`` -- an annotation
    that cannot resolve, which fails open while looking configured."""
    config = {**OIDC_CONFIG, "auth_proxy_host": ""}
    assert oidc_auth_for_cluster(_cluster(oidc_auth_config=config)) is None


def test_partial_oidc_config_is_not_gated():
    config = {"auth_proxy_host": "auth.example.net"}
    assert oidc_auth_for_cluster(_cluster(oidc_auth_config=config)) is None


# ---- cognito_auth_for_cluster ---------------------------------------


def test_complete_alb_config_yields_the_cognito_gate():
    auth = cognito_auth_for_cluster(_cluster(ingress_class="alb", alb_auth_config=ALB_CONFIG))
    assert auth is not None
    assert auth.user_pool_client_id == "client-abc"


def test_blank_alb_field_is_not_gated():
    config = {**ALB_CONFIG, "user_pool_arn": ""}
    assert cognito_auth_for_cluster(_cluster(ingress_class="alb", alb_auth_config=config)) is None


# ---- auth_annotation_patch: the class decides the key set ------------


def test_nginx_cluster_patches_the_central_auth_keys():
    patch = auth_annotation_patch(_cluster(oidc_auth_config=OIDC_CONFIG))
    assert set(patch) == set(NGINX_AUTH_ANNOTATION_KEYS)
    assert patch["nginx.ingress.kubernetes.io/auth-url"] == ("https://auth.astrolift.example.net/oauth2/auth")


def test_nginx_cluster_without_config_clears_the_central_auth_keys():
    """Clearing the config is the supported way to take a gate off, so
    the keys are patched to None rather than omitted -- omitting them
    would leave a stale gate on the live Ingress."""
    patch = auth_annotation_patch(_cluster())
    assert set(patch) == set(NGINX_AUTH_ANNOTATION_KEYS)
    assert set(patch.values()) == {None}


def test_alb_cluster_still_patches_the_cognito_keys():
    patch = auth_annotation_patch(_cluster(ingress_class="alb", alb_auth_config=ALB_CONFIG))
    assert set(patch) == set(AUTH_ANNOTATION_KEYS)
    assert patch["alb.ingress.kubernetes.io/auth-type"] == "cognito"


def test_patch_never_touches_the_other_class_keys():
    """A cluster serves one controller. Clearing the keys of a class no
    renderer on this cluster emits would be editing annotations the
    reconcile does not own."""
    nginx_patch = auth_annotation_patch(_cluster(oidc_auth_config=OIDC_CONFIG))
    assert not set(nginx_patch) & set(AUTH_ANNOTATION_KEYS)
    alb_patch = auth_annotation_patch(_cluster(ingress_class="alb", alb_auth_config=ALB_CONFIG))
    assert not set(alb_patch) & set(NGINX_AUTH_ANNOTATION_KEYS)


def test_traefik_cluster_uses_the_central_auth_keys():
    """Every non-alb class reads oidc_auth_config; the reconcile must not
    special-case only the literal string "nginx"."""
    patch = auth_annotation_patch(_cluster(ingress_class="traefik", oidc_auth_config=OIDC_CONFIG))
    assert set(patch) == set(NGINX_AUTH_ANNOTATION_KEYS)
    assert patch["nginx.ingress.kubernetes.io/auth-signin"].startswith(
        "https://auth.astrolift.example.net/oauth2/start?rd=https://"
    )
