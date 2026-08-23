"""
A cluster cannot lose its auth gate by accident (#1616).

Each ingress class reads its own auth config and ignores the other's: ALB
uses the Cognito annotations from ``alb_auth_config``, every other class
uses the oauth2-proxy annotations from ``oidc_auth_config``. Flipping
``ingress_class`` therefore drops the old gate and renders nothing in its
place, and until now ``oidc_auth_config`` was on neither
``TenantClusterType`` nor ``UpdateTenantClusterInput``, so the replacement
could not be installed through the API at all. The apps went public with
no warning and no log line.

The registration command had the same fail-open by another route:
``oidc_auth_config`` sat in ``update_or_create(defaults=...)``, so a re-run
whose environment had lost any one of the three OIDC vars reset a working
config to null.
"""

from __future__ import annotations

import uuid
from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.management import call_command

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.mutations import ClustersMutation, UpdateTenantClusterInput
from astrolift_clusters.schema.types import redact_oidc_auth_config
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
    "discovery_url": "https://dex.acme.example/dex",
    "client_id": "astrolift-proxy",
    "cookie_secret": "s3cret-signing-key",
    "auth_proxy_host": "auth.acme.example",
    "upstream_connector": "google",
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
        name="aws", slug=f"aws-{uuid.uuid4().hex[:6]}", capabilities_manifest={}, config_schema={}
    )


@pytest.fixture
def plugin_k8s():
    """The registration command resolves its plugin by slug, so this one
    has to carry the real name."""
    return ProviderPlugin.objects.create(
        name="k8s_native", slug="k8s_native", capabilities_manifest={}, config_schema={}
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
        "ingress_class": "alb",
        "alb_auth_config": ALB_CONFIG,
    }
    fields.update(over)
    return TenantCluster.objects.create(**fields)


def _info():
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=None), user=None))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _update(org, cluster, **kwargs):
    with _ctx(org):
        return ClustersMutation().update_tenant_cluster(
            _info(), UpdateTenantClusterInput(id=GUID(str(cluster.guid)), **kwargs)
        )


# ---- the fail-open ------------------------------------------------


def test_flipping_off_alb_without_an_oidc_config_is_refused(org, plugin, permission_resolver):
    """The reported defect. Before the fix this returned ok=True and every
    app on the cluster rendered with no auth annotations at all."""
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    cluster = _cluster(org, plugin)

    result = _update(org, cluster, ingress_class="nginx")

    assert result.ok is False
    assert result.errors and result.errors[0].code == "PRECONDITION"
    assert "oidcAuthConfig" in result.errors[0].message

    cluster.refresh_from_db()
    assert cluster.ingress_class == "alb", "the refused change must not persist"


def test_flipping_onto_alb_without_a_cognito_config_is_refused(org, plugin, permission_resolver):
    """Symmetric. Auditing the reverse direction rather than only the
    reported one, since both classes ignore the other's config."""
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    cluster = _cluster(org, plugin, ingress_class="nginx", alb_auth_config=None, oidc_auth_config=OIDC_CONFIG)

    result = _update(org, cluster, ingress_class="alb")

    assert result.ok is False
    assert result.errors[0].code == "PRECONDITION"
    assert "albAuthConfig" in result.errors[0].message


def test_flipping_with_the_replacement_gate_in_the_same_call_succeeds(org, plugin, permission_resolver):
    """The migration this unblocks. Without oidc_auth_config on the input
    there was no way to perform it safely at all."""
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    cluster = _cluster(org, plugin)

    result = _update(org, cluster, ingress_class="nginx", oidc_auth_config=OIDC_CONFIG)

    assert result.ok is True, result.errors
    cluster.refresh_from_db()
    assert cluster.ingress_class == "nginx"
    assert cluster.oidc_auth_config == OIDC_CONFIG


def test_an_incomplete_oidc_config_does_not_count_as_a_gate(org, plugin, permission_resolver):
    """The renderer emits the auth annotations only when all three of
    discovery_url, client_id and auth_proxy_host are present, so anything
    less is not a gate and must not be accepted as one."""
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    cluster = _cluster(org, plugin)

    result = _update(
        org,
        cluster,
        ingress_class="nginx",
        oidc_auth_config={"discovery_url": "https://dex.acme.example/dex"},
    )

    assert result.ok is False
    assert result.errors[0].code == "PRECONDITION"


def test_an_ungated_cluster_may_change_class_freely(org, plugin, permission_resolver):
    """The guard protects an existing gate. A cluster that never had one
    is not made harder to configure."""
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    cluster = _cluster(org, plugin, alb_auth_config=None)

    result = _update(org, cluster, ingress_class="nginx")

    assert result.ok is True, result.errors


def test_clearing_the_gate_explicitly_is_still_allowed(org, plugin, permission_resolver):
    """Documented behaviour preserved: reconcileClusterIngresses states
    that nulling alb_auth_config strips the annotations and leaves the apps
    public. An operator who does that is asking for it; one who changes
    ingress class is not."""
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    cluster = _cluster(org, plugin)

    result = _update(org, cluster, alb_auth_config=None)

    assert result.ok is True, result.errors
    cluster.refresh_from_db()
    assert cluster.alb_auth_config is None


# ---- the read side must not leak the signing key ------------------


def test_the_cookie_secret_is_never_read_back(org, plugin, permission_resolver):
    """It signs the oauth2-proxy session cookie, so anyone who can read it
    can mint a session. Exposing the config to fix a fail-open would have
    traded one hole for another."""
    view = redact_oidc_auth_config(OIDC_CONFIG)

    assert "cookie_secret" not in view
    assert OIDC_CONFIG["cookie_secret"] not in repr(view)
    # But an operator still has to be able to see whether the gate is set,
    # which is what makes a class flip safe to attempt.
    assert view["cookie_secret_set"] is True
    assert view["auth_proxy_host"] == "auth.acme.example"


def test_an_unset_config_reads_as_null(org, plugin):
    assert redact_oidc_auth_config(None) is None
    assert redact_oidc_auth_config({}) is None


def test_a_config_missing_its_secret_reports_that(org, plugin):
    view = redact_oidc_auth_config({"discovery_url": "https://d", "client_id": "c"})
    assert view["cookie_secret_set"] is False


# ---- the registration command's own fail-open ---------------------


def _register(slug: str, monkeypatch, *, oidc: bool):
    """Run the real command, with or without the three OIDC env vars."""
    for var in ("DISCOVERY_URL", "CLIENT_ID", "COOKIE_SECRET", "AUTH_PROXY_HOST"):
        monkeypatch.delenv(f"ASTROLIFT_CLUSTER_OIDC_{var}", raising=False)
    if oidc:
        monkeypatch.setenv("ASTROLIFT_CLUSTER_OIDC_DISCOVERY_URL", OIDC_CONFIG["discovery_url"])
        monkeypatch.setenv("ASTROLIFT_CLUSTER_OIDC_CLIENT_ID", OIDC_CONFIG["client_id"])
        monkeypatch.setenv("ASTROLIFT_CLUSTER_OIDC_COOKIE_SECRET", OIDC_CONFIG["cookie_secret"])
        monkeypatch.setenv("ASTROLIFT_CLUSTER_OIDC_AUTH_PROXY_HOST", OIDC_CONFIG["auth_proxy_host"])
    call_command(
        "register_tenant_cluster",
        "--slug",
        slug,
        "--plugin-slug",
        "k8s_native",
        "--auth-method",
        "kubeconfig",
        "--endpoint",
        "https://invalid",
        "--ingress-class",
        "nginx",
        stdout=StringIO(),
    )


def test_registration_writes_the_gate_when_the_vars_are_present(plugin_k8s, monkeypatch):
    slug = f"reg-{uuid.uuid4().hex[:6]}"
    monkeypatch.setenv("ASTROLIFT_CLUSTER_KUBECONFIG", "fake")

    _register(slug, monkeypatch, oidc=True)

    row = TenantCluster.all_objects.get(slug=slug)
    assert row.oidc_auth_config["client_id"] == OIDC_CONFIG["client_id"]


def test_a_reregistration_without_the_oidc_vars_keeps_the_existing_gate(plugin_k8s, monkeypatch):
    """The second fail-open. oidc_auth_config used to sit in
    update_or_create(defaults=...) unconditionally, so a CI setup re-run
    whose environment had lost any one of the three OIDC vars reset a
    working config to null and every later deploy rendered unauthenticated
    Ingresses. Clearing a live gate should take a deliberate act."""
    slug = f"reg-{uuid.uuid4().hex[:6]}"
    monkeypatch.setenv("ASTROLIFT_CLUSTER_KUBECONFIG", "fake")
    _register(slug, monkeypatch, oidc=True)
    assert TenantCluster.all_objects.get(slug=slug).oidc_auth_config is not None

    _register(slug, monkeypatch, oidc=False)

    row = TenantCluster.all_objects.get(slug=slug)
    assert row.oidc_auth_config is not None, "a partially-configured re-run cleared a live auth gate"
    assert row.oidc_auth_config["client_id"] == OIDC_CONFIG["client_id"]
