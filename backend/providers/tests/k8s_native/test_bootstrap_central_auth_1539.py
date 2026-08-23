"""The auth host is rendered from the cluster row (#1539).

Before this, the ``oauth2-proxy`` bootstrap component shipped
``clientID: "astrolift-proxy"`` and no cookie domain, whitelist domain or
redirect URL -- the three settings the "one callback, one cookie, many
apps" design is made of. Every install therefore hand-built its own auth
host and the recipe was decoration. These tests pin that the recipe now
carries what the operator declared.
"""

from __future__ import annotations

from _sdk.cluster import ClusterContext
from k8s_native.central_auth import (
    CENTRAL_AUTH_SECRET_NAME,
    cookie_scope_for,
    issuer_from_discovery_url,
)
from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig

OIDC_CONFIG = {
    "discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc",
    "client_id": "1qqhi5irhften18gbbpailuo8v",
    "cookie_secret": "signing-key-do-not-publish",
    "auth_proxy_host": "auth.astrolift.smdinfra.net",
    "upstream_connector": "oidc",
}


def _component(oidc_auth_config):
    driver = K8sNativeClusterDriver(config=K8sNativeConfig())
    ctx = ClusterContext(
        slug="tenant",
        auth_method="kubeconfig",
        oidc_auth_config=oidc_auth_config,
    )
    for component in driver.bootstrap_components(ctx):
        if component.key == "oauth2-proxy":
            return component
    raise AssertionError("oauth2-proxy component missing from the recipe")


# ---- cookie_scope_for -----------------------------------------------


def test_cookie_scope_is_the_parent_zone():
    """The whole mechanism: a cookie on the parent zone is presented on
    every app subdomain under it, so one login covers them all."""
    assert cookie_scope_for("auth.astrolift.smdinfra.net") == ".astrolift.smdinfra.net"


def test_cookie_scope_of_a_two_label_host_is_empty():
    """No parent worth scoping to. Emitting ``.net`` would be a cookie
    scoped to a public suffix, which browsers reject anyway."""
    assert cookie_scope_for("auth.net") == ""


def test_cookie_scope_of_empty_host_is_empty():
    assert cookie_scope_for("") == ""


# ---- issuer_from_discovery_url --------------------------------------


def test_discovery_suffix_is_stripped():
    """oauth2-proxy appends the well-known path itself; passing the
    discovery URL through doubles it and the proxy cannot start."""
    assert (
        issuer_from_discovery_url("https://issuer.example/.well-known/openid-configuration") == "https://issuer.example"
    )


def test_plain_issuer_passes_through():
    assert issuer_from_discovery_url("https://issuer.example") == "https://issuer.example"


def test_trailing_slash_is_normalised():
    assert issuer_from_discovery_url("https://issuer.example/") == "https://issuer.example"


# ---- the rendered component -----------------------------------------


def test_component_carries_the_single_callback():
    args = _component(OIDC_CONFIG).helm_values["extraArgs"]
    assert args["redirect-url"] == "https://auth.astrolift.smdinfra.net/oauth2/callback"


def test_component_scopes_the_cookie_to_the_parent_zone():
    args = _component(OIDC_CONFIG).helm_values["extraArgs"]
    assert args["cookie-domain"] == ".astrolift.smdinfra.net"


def test_component_whitelists_the_parent_zone_for_redirects():
    """The Ingress annotations hand oauth2-proxy the app's full URL as
    ``rd``; without the whitelist it refuses the redirect and strands the
    user on the auth host after a successful login."""
    args = _component(OIDC_CONFIG).helm_values["extraArgs"]
    assert args["whitelist-domain"] == ".astrolift.smdinfra.net"


def test_component_uses_the_declared_client_id():
    assert _component(OIDC_CONFIG).helm_values["config"]["clientID"] == OIDC_CONFIG["client_id"]


def test_component_points_at_the_declared_issuer():
    args = _component(OIDC_CONFIG).helm_values["extraArgs"]
    assert args["oidc-issuer-url"] == OIDC_CONFIG["discovery_url"]


def test_component_serves_on_the_declared_host():
    ingress = _component(OIDC_CONFIG).helm_values["ingress"]
    assert ingress["hosts"] == ["auth.astrolift.smdinfra.net"]
    assert ingress["tls"][0]["hosts"] == ["auth.astrolift.smdinfra.net"]


def test_component_never_publishes_the_cookie_secret():
    """``helm_values`` is returned to operators over GraphQL and the
    cookie secret signs the session cookie, so anyone who could read it
    could mint a session. The release reads it from a Secret instead."""
    values = _component(OIDC_CONFIG).helm_values
    assert values["config"]["existingSecret"] == CENTRAL_AUTH_SECRET_NAME
    assert "signing-key-do-not-publish" not in repr(values)
    assert "cookieSecret" not in values["config"]


def test_component_enabled_once_the_config_is_complete():
    assert _component(OIDC_CONFIG).default_enabled is True


def test_component_disabled_without_a_config():
    """Nothing to render an auth host from, so the recipe must not offer
    to install one that would refuse every request."""
    assert _component({}).default_enabled is False


def test_component_omits_host_flags_without_a_config():
    values = _component({}).helm_values
    assert values["ingress"]["hosts"] == []
    assert "redirect-url" not in values["extraArgs"]
    assert "cookie-domain" not in values["extraArgs"]


# ---- every cloud driver offers the same auth host --------------------


def test_eks_recipe_offers_the_central_auth_host():
    """Astrolift's tenant runtime is EKS. A recipe only the vanilla-k8s
    driver carried would leave the auth host unbuildable on the clusters
    that actually serve tenant apps -- which is how the first install
    ended up hand-assembling it with Flux."""
    from unittest.mock import MagicMock

    from _sdk.cluster import ClusterContext as Ctx
    from aws.cluster_eks import EKSClusterDriver, EKSConfig

    eks = MagicMock()
    eks.describe_cluster.return_value = {"cluster": {"resourcesVpcConfig": {"vpcId": "vpc-1"}}}
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Account": "123456789012"}
    ec2 = MagicMock()
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    driver = EKSClusterDriver(
        config=EKSConfig(region="us-west-2", cluster_name="prod"),
        eks_client=eks,
        sts_client=sts,
        ec2_client=ec2,
        k8s_client_factory=lambda **kw: MagicMock(),
    )

    components = driver.bootstrap_components(
        Ctx(slug="aws-prod", auth_method="exec_plugin", oidc_auth_config=OIDC_CONFIG)
    )
    component = next(c for c in components if c.key == "oauth2-proxy")

    assert component.default_enabled is True
    assert component.helm_values["extraArgs"]["redirect-url"] == ("https://auth.astrolift.smdinfra.net/oauth2/callback")
    assert component.helm_values["extraArgs"]["cookie-domain"] == ".astrolift.smdinfra.net"


def test_eks_recipe_leaves_the_auth_host_off_without_a_config():
    """An ALB-only cluster has no nginx controller to gate against, so
    the component must be inert rather than installed-and-broken."""
    from unittest.mock import MagicMock

    from _sdk.cluster import ClusterContext as Ctx
    from aws.cluster_eks import EKSClusterDriver, EKSConfig

    eks = MagicMock()
    eks.describe_cluster.return_value = {"cluster": {"resourcesVpcConfig": {"vpcId": "vpc-1"}}}
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Account": "123456789012"}
    ec2 = MagicMock()
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    driver = EKSClusterDriver(
        config=EKSConfig(region="us-west-2", cluster_name="prod"),
        eks_client=eks,
        sts_client=sts,
        ec2_client=ec2,
        k8s_client_factory=lambda **kw: MagicMock(),
    )

    components = driver.bootstrap_components(Ctx(slug="aws-prod", auth_method="exec_plugin"))
    component = next(c for c in components if c.key == "oauth2-proxy")
    assert component.default_enabled is False
