"""The operator can drive the central auth gate, not just render it (#1539).

Two operator affordances existed for edge auth and both were ALB-only:
``reconcileClusterIngresses`` refused any other ingress class outright,
and the GitOps write-back mirrored only ``alb_auth_config``. Under the
delivered nginx design that left no way to push an auth change onto
running Ingresses -- Stage B on the first install had to hand-annotate
them -- and flipping a cluster onto nginx *stripped* ``[ingress.auth]``
from every bound app's manifest, so the repo recorded "no auth" for apps
that were in fact gated.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.mutations import (
    ClustersMutation,
    ReconcileClusterIngressesInput,
)
from astrolift_clusters.services.toml_writeback import (
    ingress_auth_section_for_cluster,
    oidc_auth_section_from_db,
)
from astrolift_graphql import GUID
from astrolift_identity.models import Organization
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

ALB_CONFIG = {
    "user_pool_arn": "arn:aws:cognito-idp:us-west-2:111122223333:userpool/us-west-2_abc",
    "user_pool_client_id": "client-abc",
    "user_pool_domain": "acme-auth",
}
OIDC_CONFIG = {
    "discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc",
    "client_id": "1qqhi5irhften18gbbpailuo8v",
    "cookie_secret": "signing-key-do-not-commit",
    "auth_proxy_host": "auth.apps.example.net",
    "upstream_connector": "oidc",
}


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def plugin():
    return ProviderPlugin.objects.create(
        name="k8s", slug=f"k8s-{uuid.uuid4().hex[:6]}", capabilities_manifest={}, config_schema={}
    )


def _cluster(org, plugin, **over):
    fields = {
        "organization": org,
        "slug": f"c-{uuid.uuid4().hex[:6]}",
        "name": "dev",
        "provider_plugin": plugin,
        "provider_config": {},
        "region": "us-west-2",
        "endpoint": "https://invalid",
        "auth_method": TenantCluster.AuthMethod.KUBECONFIG,
        "auth_config": {"kubeconfig": "fake"},
        "is_active": True,
        "ingress_class": "nginx",
        "oidc_auth_config": OIDC_CONFIG,
    }
    fields.update(over)
    return TenantCluster.objects.create(**fields)


def _info():
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=None), user=None))


# ---- reconcile is no longer ALB-only --------------------------------


def test_reconcile_accepts_an_nginx_cluster(org, plugin, permission_resolver, monkeypatch):
    """It used to fail PRECONDITION with "reconcile only supported for alb
    ingressClass", which is why live Ingresses had to be hand-annotated."""
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    cluster = _cluster(org, plugin)

    seen = {}

    def _fake_reconcile(target):
        seen["cluster"] = target
        return {"reconciled": 3, "skipped": 1, "errors": []}

    monkeypatch.setattr("core.ingress_reconcile.reconcile_cluster_ingresses", _fake_reconcile)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersMutation().reconcile_cluster_ingresses(
            _info(), ReconcileClusterIngressesInput(cluster_id=GUID(str(cluster.guid)))
        )

    assert result.ok is True, result.errors
    assert result.data.reconciled_count == 3
    assert seen["cluster"].pk == cluster.pk


def test_reconcile_still_accepts_an_alb_cluster(org, plugin, permission_resolver, monkeypatch):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    cluster = _cluster(org, plugin, ingress_class="alb", alb_auth_config=ALB_CONFIG, oidc_auth_config=None)

    monkeypatch.setattr(
        "core.ingress_reconcile.reconcile_cluster_ingresses",
        lambda target: {"reconciled": 1, "skipped": 0, "errors": []},
    )

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersMutation().reconcile_cluster_ingresses(
            _info(), ReconcileClusterIngressesInput(cluster_id=GUID(str(cluster.guid)))
        )

    assert result.ok is True, result.errors


# ---- GitOps write-back mirrors the gate actually in force ------------


def test_writeback_section_follows_the_nginx_gate():
    cluster = SimpleNamespace(ingress_class="nginx", alb_auth_config=None, oidc_auth_config=OIDC_CONFIG)
    section = ingress_auth_section_for_cluster(cluster)
    assert section["kind"] == "oidc"
    assert section["auth_proxy_host"] == "auth.apps.example.net"


def test_writeback_section_follows_the_alb_gate():
    cluster = SimpleNamespace(ingress_class="alb", alb_auth_config=ALB_CONFIG, oidc_auth_config=OIDC_CONFIG)
    section = ingress_auth_section_for_cluster(cluster)
    assert section["kind"] == "cognito"
    assert section["user_pool_arn"] == ALB_CONFIG["user_pool_arn"]


def test_writeback_no_longer_strips_auth_when_a_cluster_moves_to_nginx():
    """The regression this closes: with only ``alb_auth_config`` mirrored,
    a cluster on nginx read as null and the writer removed
    ``[ingress.auth]`` from every bound app's astrolift.toml."""
    cluster = SimpleNamespace(ingress_class="nginx", alb_auth_config=None, oidc_auth_config=OIDC_CONFIG)
    assert ingress_auth_section_for_cluster(cluster) is not None


def test_writeback_section_never_carries_the_cookie_secret():
    """This table is committed to the customer's own git repo."""
    section = oidc_auth_section_from_db(OIDC_CONFIG)
    assert "cookie_secret" not in section
    assert "signing-key-do-not-commit" not in repr(section)


def test_writeback_section_absent_for_an_incomplete_oidc_config():
    assert oidc_auth_section_from_db({"auth_proxy_host": "auth.example.net"}) is None
    assert oidc_auth_section_from_db({**OIDC_CONFIG, "client_id": ""}) is None
    assert oidc_auth_section_from_db(None) is None
