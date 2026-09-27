"""The EKS recipe carries everything the central auth host needs (#2055).

It offered the oauth2-proxy auth host (#1539) and nothing it stands on: no
nginx controller to gate through, no ClusterIssuer for the certificates every
nginx-class Ingress names, no credentials Secret, and a dependsOn on a Dex
release EKS never has, which Flux holds forever. The first install built all
of it by hand. These pin the recipe that replaces the hand-work, and that a
cluster which never asked for the edge sees the recipe it always had.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from _sdk.cluster import BootstrapComponent, ClusterContext
from aws.cluster_eks import EKSClusterDriver, EKSConfig
from k8s_native.central_auth import (
    CENTRAL_AUTH_SECRET_NAME,
    EDGE_CLUSTER_ISSUER,
    LETSENCRYPT_PROD_SERVER,
    central_auth_secret_manifest,
    edge_cluster_issuer,
    validate_acme_email,
)

COGNITO = {
    "discovery_url": ("https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc/.well-known/openid-configuration"),
    "client_id": "central-client-id",
    "auth_proxy_host": "auth.apps.example.net",
}
CLIENT_SECRET = "client-secret-2055-do-not-publish"
COOKIE_SECRET = "cookie-secret-2055-do-not-publish"
WITH_SECRETS = {**COGNITO, "client_secret": CLIENT_SECRET, "cookie_secret": COOKIE_SECRET}

# The EKS recipe as it stood before #2055, in order. An ALB-only cluster
# must keep seeing exactly this.
ALB_ONLY_KEYS = [
    "aws-load-balancer-controller",
    "external-dns",
    "metrics-server",
    "aws-ebs-csi-driver",
    "aws-mountpoint-s3-csi-driver",
    "kube-prometheus-stack",
    "cloudwatch-exporter",
    "cert-manager",
    "knative-serving",
    "oauth2-proxy",
]


def _driver() -> EKSClusterDriver:
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Account": "123456789012"}
    eks = MagicMock()
    eks.describe_cluster.return_value = {"cluster": {"resourcesVpcConfig": {"vpcId": "vpc-123"}}}
    ec2 = MagicMock()
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    return EKSClusterDriver(
        config=EKSConfig(region="us-west-2", cluster_name="astrolift-eks"),
        eks_client=eks,
        sts_client=sts,
        ec2_client=ec2,
        k8s_client_factory=lambda **kw: MagicMock(),
    )


def _recipe(*, ingress_class: str = "alb", oidc: dict[str, Any] | None = None) -> list[BootstrapComponent]:
    ctx = ClusterContext(
        slug="aws-prod",
        auth_method="exec_plugin",
        ingress_class=ingress_class,
        oidc_auth_config=oidc or {},
    )
    return _driver().bootstrap_components(ctx)


def _by_key(components: list[BootstrapComponent]) -> dict[str, BootstrapComponent]:
    return {c.key: c for c in components}


# ---- an ALB-only cluster is untouched --------------------------------


@pytest.mark.parametrize(
    "oidc",
    [None, {"discovery_url": COGNITO["discovery_url"]}, {**COGNITO, "auth_proxy_host": ""}],
    ids=["no-config", "discovery-only", "blank-host"],
)
def test_alb_only_cluster_keeps_the_recipe_it_had(oidc):
    """No edge component, and cert-manager exactly as before: off, no
    issuer, the same option. An incomplete config is not a request for the
    edge, the same test the render gate applies."""
    components = _recipe(oidc=oidc)
    assert [c.key for c in components] == ALB_ONLY_KEYS

    cert_manager = _by_key(components)["cert-manager"]
    assert cert_manager.default_enabled is False
    assert cert_manager.post_install_manifests == []
    assert [o.key for o in cert_manager.options] == ["mode"]
    assert cert_manager.rationale.startswith("EKS operators typically use ACM")


# ---- the edge is offered when asked for ------------------------------


def test_a_complete_auth_config_offers_ingress_nginx_on_by_default():
    """Stage A: the cluster is still ALB, but it now has an auth host to serve."""
    component = _by_key(_recipe(oidc=COGNITO))["ingress-nginx"]
    assert component.default_enabled is True
    assert component.chart_name == "ingress-nginx"
    assert component.chart_repo_url == "https://kubernetes.github.io/ingress-nginx"
    assert component.chart_version


@pytest.mark.parametrize("ingress_class", ["nginx", "ingress-nginx"])
def test_an_nginx_family_class_offers_the_edge_without_an_auth_config(ingress_class):
    components = _by_key(_recipe(ingress_class=ingress_class))
    assert components["ingress-nginx"].default_enabled is True
    assert components["cert-manager"].default_enabled is True


def test_the_edge_sits_before_the_auth_host():
    keys = [c.key for c in _recipe(oidc=COGNITO)]
    assert keys.index("ingress-nginx") < keys.index("oauth2-proxy")


def test_the_nlb_passes_tls_through_to_nginx():
    """TLS terminated at the NLB hands nginx plain HTTP with no
    X-Forwarded-Proto, and nginx redirects every request forever (hit live).
    So: no certificate or TLS listener on the Service, and 443 left on the
    chart's https target port."""
    service = _by_key(_recipe(oidc=COGNITO))["ingress-nginx"].helm_values["controller"]["service"]
    assert service["annotations"] == {
        "service.beta.kubernetes.io/aws-load-balancer-type": "external",
        "service.beta.kubernetes.io/aws-load-balancer-nlb-target-type": "ip",
        "service.beta.kubernetes.io/aws-load-balancer-scheme": "internet-facing",
    }
    assert "targetPorts" not in service


def test_the_controller_holds_the_proxy_cookies():
    """The session cookies scoped to the parent zone ride every request and
    the auth subrequest (#1725)."""
    config = _by_key(_recipe(oidc=COGNITO))["ingress-nginx"].helm_values["controller"]["config"]
    assert config["large-client-header-buffers"] == "8 64k"
    assert config["proxy-buffer-size"] == "16k"


def test_the_controller_accepts_the_platforms_snippet_annotations():
    """Logout, the gateway secret, edge identity headers and the paused-app
    response are snippet annotations. Refused, the controller's admission
    webhook rejects the Ingress and the deploy fails."""
    controller = _by_key(_recipe(oidc=COGNITO))["ingress-nginx"].helm_values["controller"]
    assert controller["allowSnippetAnnotations"] is True
    assert controller["config"]["annotations-risk-level"] == "Critical"


def test_the_edge_waits_for_the_load_balancer_controller():
    """The NLB comes from the AWS Load Balancer Controller, whose Service
    webhook has to be serving first."""
    assert "aws-load-balancer-controller" in _by_key(_recipe(oidc=COGNITO))["ingress-nginx"].depends_on


# ---- the issuer the Ingresses name -----------------------------------


def test_cert_manager_brings_the_issuer_every_nginx_ingress_names():
    """Both render paths annotate ``cert-manager.io/cluster-issuer:
    letsencrypt-prod`` and so does the auth host. Nothing rendered it, so no
    certificate on the edge was ever issued."""
    cert_manager = _by_key(_recipe(oidc=COGNITO))["cert-manager"]
    assert cert_manager.default_enabled is True
    assert cert_manager.post_install_manifests == [edge_cluster_issuer()]

    issuer = cert_manager.post_install_manifests[0]
    assert issuer["apiVersion"] == "cert-manager.io/v1"
    assert issuer["kind"] == "ClusterIssuer"
    assert issuer["metadata"]["name"] == EDGE_CLUSTER_ISSUER == "letsencrypt-prod"
    assert issuer["spec"]["acme"]["server"] == LETSENCRYPT_PROD_SERVER


def test_the_issuer_solves_http01_through_nginx():
    """HTTP-01 through the nginx class needs no Route53 access."""
    issuer = _by_key(_recipe(oidc=COGNITO))["cert-manager"].post_install_manifests[0]
    assert issuer["spec"]["acme"]["solvers"] == [{"http01": {"ingress": {"ingressClassName": "nginx"}}}]


def test_the_acme_contact_comes_from_the_cluster_config():
    issuer = _by_key(_recipe(oidc={**COGNITO, "acme_email": "ops@example.net"}))["cert-manager"]
    assert issuer.post_install_manifests[0]["spec"]["acme"]["email"] == "ops@example.net"


def test_no_acme_contact_is_invented():
    """Unset means no contact on the account, never a default address."""
    issuer = _by_key(_recipe(oidc=COGNITO))["cert-manager"].post_install_manifests[0]
    assert "email" not in issuer["spec"]["acme"]


def test_the_issuer_select_is_not_offered_on_the_edge():
    """It changes nothing there, and a picked value lands in the chart's
    values, whose schema refuses unknown keys."""
    assert _by_key(_recipe(oidc=COGNITO))["cert-manager"].options == []


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("ops@example.net", True),
        ("", True),
        (None, True),
        ("not-an-address", False),
        ("a@b", False),
        ("ops@example.net, dev@example.net", False),
        (["ops@example.net"], False),
    ],
)
def test_the_acme_contact_is_validated_where_it_is_written(value, valid):
    config = {**COGNITO, "acme_email": value}
    if valid:
        validate_acme_email(config)
        return
    with pytest.raises(ValueError, match="acme_email") as caught:
        validate_acme_email(config)
    assert str(value) not in str(caught.value)


# ---- the auth host's dependencies ------------------------------------


def test_the_auth_host_does_not_wait_for_dex_behind_an_external_issuer():
    """EKS has no Dex component. A dependsOn on it names a release that is
    never installed, and Flux holds the auth host forever."""
    assert _by_key(_recipe(oidc=COGNITO))["oauth2-proxy"].depends_on == ["ingress-nginx"]


def test_the_auth_host_waits_for_dex_when_dex_is_the_issuer():
    component = _by_key(_recipe(oidc={**COGNITO, "kind": "dex"}))["oauth2-proxy"]
    assert set(component.depends_on) == {"ingress-nginx", "dex"}


# ---- the credentials never ride the recipe ---------------------------


def test_the_recipe_never_carries_the_proxy_credentials():
    """The recipe is served to operators over GraphQL. The secrets reach the
    cluster only through the install activity."""
    components = _recipe(oidc=WITH_SECRETS)
    rendered = repr(components) + json.dumps([c.helm_values for c in components], default=str)
    for secret in (CLIENT_SECRET, COOKIE_SECRET):
        assert secret not in rendered
        assert base64.b64encode(secret.encode()).decode() not in rendered
    assert not [
        m
        for c in components
        for m in c.post_install_manifests
        if (m.get("metadata") or {}).get("name") == CENTRAL_AUTH_SECRET_NAME
    ]


# ---- the Secret the install writes -----------------------------------


def test_the_secret_carries_every_key_the_chart_reads():
    """With existingSecret the chart reads client-id from the Secret too,
    whatever config.clientID says, so a Secret without it never starts."""
    manifest = central_auth_secret_manifest(WITH_SECRETS, namespace="astrolift-system")
    assert manifest is not None
    assert manifest["kind"] == "Secret"
    assert manifest["metadata"]["name"] == CENTRAL_AUTH_SECRET_NAME
    assert manifest["metadata"]["namespace"] == "astrolift-system"
    decoded = {k: base64.b64decode(v).decode() for k, v in manifest["data"].items()}
    assert decoded == {
        "client-id": COGNITO["client_id"],
        "client-secret": CLIENT_SECRET,
        "cookie-secret": COOKIE_SECRET,
    }


def test_the_release_reads_the_secret_the_install_writes():
    component = _by_key(_recipe(oidc=WITH_SECRETS))["oauth2-proxy"]
    assert component.helm_values["config"]["existingSecret"] == CENTRAL_AUTH_SECRET_NAME


@pytest.mark.parametrize("missing", ["client_id", "client_secret", "cookie_secret"])
@pytest.mark.parametrize("blank", [None, ""])
def test_no_secret_is_built_from_a_partial_config(missing, blank):
    """A partial Secret cannot start the proxy, and would overwrite a
    hand-made one that can."""
    config = {**WITH_SECRETS, missing: blank}
    assert central_auth_secret_manifest(config, namespace="astrolift-system") is None


# ---- external-dns follows the edge -----------------------------------


def test_external_dns_publishes_nginx_class_ingresses():
    """The auth host and every app on the edge are nginx-class Ingresses.
    No class or annotation filter, and the kingpin negation for target
    health: ``--aws-evaluate-target-health=false`` crashloops the pod."""
    values = _by_key(_recipe(oidc=COGNITO))["external-dns"].helm_values
    assert "ingress" in values["sources"]
    args = values["extraArgs"]
    assert "--no-aws-evaluate-target-health" in args
    assert not any(a.startswith("--aws-evaluate-target-health") for a in args)
    assert not any("ingress-class" in a or "annotation-filter" in a for a in args)
    assert not {"annotationFilter", "ingressClassFilters", "labelFilter"} & set(values)
