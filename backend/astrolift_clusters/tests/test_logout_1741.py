"""Operator configuration reaches the recipe, deploy renderer and live patcher."""

import uuid
from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.mutations import ClustersMutation, UpdateTenantClusterInput
from astrolift_clusters.schema.types import redact_oidc_auth_config
from astrolift_clusters.services.toml_writeback import oidc_auth_section_from_db
from astrolift_graphql import GUID
from astrolift_identity.models import Organization
from core.app_deploy import oidc_auth_for_cluster
from core.ingress_reconcile import auth_annotation_patch
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from providers.k8s_native.ingress import K8sIngressConfig, K8sIngressDriver

pytestmark = pytest.mark.django_db

SNIPPET = "nginx.ingress.kubernetes.io/configuration-snippet"
CONFIG = {
    "discovery_url": "https://issuer.example.com",
    "client_id": "central-client",
    "auth_proxy_host": "auth.apps.example.com",
    "cookie_secret": "c" * 32,
    "gateway_secret": "g" * 64,
    "logout_url": "https://idp.example.com/logout?client_id=central-client&logout_uri=https%3A%2F%2Fauth.apps.example.com%2Fauth%2Flogged-out",
}


@pytest.fixture
def cluster():
    org = Organization.objects.create(name="Logout", slug=f"logout-{uuid.uuid4().hex[:8]}")
    plugin, _ = ProviderPlugin.objects.get_or_create(slug="k8s_native", defaults={"name": "Kubernetes"})
    return TenantCluster.objects.create(
        name="Logout",
        slug=f"logout-{uuid.uuid4().hex[:8]}",
        organization=org,
        provider_plugin=plugin,
        ingress_class="nginx",
        oidc_auth_config=CONFIG,
    )


def _update(cluster, config, org_id=None):
    request = RequestFactory().post("/graphql/")
    request.user = None
    info = SimpleNamespace(context=SimpleNamespace(user=None, request=request))
    with tenant_context(TenantContext(organization_id=org_id or cluster.organization_id)):
        return ClustersMutation().update_tenant_cluster(
            info,
            UpdateTenantClusterInput(id=GUID(str(cluster.guid)), oidc_auth_config=config),
        )


def test_operator_can_configure_and_clear_logout_without_disabling_gate(cluster, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    updated = {**CONFIG, "logout_url": "https://idp.example.com/new-logout"}
    assert _update(cluster, updated).ok
    cluster.refresh_from_db()
    assert redact_oidc_auth_config(cluster.oidc_auth_config)["logout_url"] == updated["logout_url"]
    assert oidc_auth_section_from_db(cluster.oidc_auth_config)["logout_url"] == updated["logout_url"]
    cleared = {**updated, "logout_url": ""}
    assert _update(cluster, cleared).ok
    cluster.refresh_from_db()
    assert oidc_auth_for_cluster(cluster) is not None
    assert not oidc_auth_for_cluster(cluster).logout_enabled
    assert "logout_url" not in oidc_auth_section_from_db(cluster.oidc_auth_config)


def test_invalid_update_does_not_save(cluster, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    result = _update(cluster, {**CONFIG, "logout_url": "https://idp.example/$host"})
    assert not result.ok
    cluster.refresh_from_db()
    assert cluster.oidc_auth_config == CONFIG


def test_foreign_tenant_cannot_change_logout(cluster, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    org = Organization.objects.create(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
    assert not _update(cluster, {**CONFIG, "logout_url": "https://other.example/logout"}, org.id).ok
    cluster.refresh_from_db()
    assert cluster.oidc_auth_config == CONFIG


def test_logout_update_requires_cluster_permission(cluster, permission_resolver):
    result = _update(cluster, {**CONFIG, "logout_url": "https://other.example/logout"})
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    cluster.refresh_from_db()
    assert cluster.oidc_auth_config == CONFIG


@pytest.mark.parametrize("granted", [True, False])
def test_compiled_graphql_contract_exposes_only_authorized_public_config(
    cluster, permission_resolver, granted
):
    from config.schema import schema

    if granted:
        permission_resolver.grant(Permission.CLUSTER_UPDATE)
    request = RequestFactory().post("/graphql/")
    request.user = None
    context = SimpleNamespace(user=None, request=request)
    updated = {**CONFIG, "logout_url": "https://idp.example.com/new-logout"}
    with tenant_context(TenantContext(organization_id=cluster.organization_id)):
        result = schema.execute_sync(
            """mutation($input: UpdateTenantClusterInput!) {
              updateTenantCluster(input: $input) {
                ok errors { code message } data { oidcAuthConfig }
              }
            }""",
            variable_values={"input": {"id": str(cluster.guid), "oidcAuthConfig": updated}},
            context_value=context,
        )
    assert not result.errors
    payload = result.data["updateTenantCluster"]
    assert payload["ok"] is granted
    cluster.refresh_from_db()
    if granted:
        assert payload["data"]["oidcAuthConfig"]["logout_url"] == updated["logout_url"]
        assert CONFIG["cookie_secret"] not in str(payload)
        assert CONFIG["gateway_secret"] not in str(payload)
    else:
        assert payload["errors"][0]["code"] == "PERMISSION_DENIED"
        assert cluster.oidc_auth_config == CONFIG


def test_render_and_reconcile_share_logout_and_preserve_foreign_content(cluster):
    auth = oidc_auth_for_cluster(cluster)
    driver = K8sIngressDriver(config=K8sIngressConfig(oidc_auth=auth))
    rendered = driver.render_ingress(
        app="app", workload="web", hostnames=["app.apps.example.com"], tls_strategy="letsencrypt"
    )
    snippet = rendered[0]["metadata"]["annotations"][SNIPPET]
    assert snippet == auth_annotation_patch(cluster)[SNIPPET]
    foreign = 'proxy_set_header X-App-Header "value";\n'
    first = auth_annotation_patch(cluster, foreign + snippet)[SNIPPET]
    assert first == foreign + snippet
    cluster.oidc_auth_config = {**CONFIG, "logout_url": ""}
    removed = auth_annotation_patch(cluster, first)[SNIPPET]
    assert "auth/logout" not in removed
    assert foreign in removed and "X-Astrolift-Gateway-Secret" in removed
    cluster.oidc_auth_config = None
    assert auth_annotation_patch(cluster, first)[SNIPPET] == foreign


@pytest.mark.parametrize("controller", ["alb", "traefik", "kong", "haproxy"])
def test_other_controllers_do_not_acquire_a_logout_route(cluster, controller):
    cluster.ingress_class = controller
    assert not oidc_auth_for_cluster(cluster).logout_enabled
    assert "auth/logout" not in str(auth_annotation_patch(cluster))


def _register(cluster, monkeypatch, **extra):
    env = {
        "ASTROLIFT_CLUSTER_OIDC_DISCOVERY_URL": CONFIG["discovery_url"],
        "ASTROLIFT_CLUSTER_OIDC_CLIENT_ID": CONFIG["client_id"],
        "ASTROLIFT_CLUSTER_OIDC_COOKIE_SECRET": CONFIG["cookie_secret"],
        "ASTROLIFT_CLUSTER_OIDC_AUTH_PROXY_HOST": CONFIG["auth_proxy_host"],
        **extra,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    call_command("register_tenant_cluster", slug=cluster.slug, plugin_slug="k8s_native", stdout=StringIO())


def test_reregistration_preserves_operator_url_and_env_can_replace_it(cluster, monkeypatch):
    monkeypatch.delenv("ASTROLIFT_CLUSTER_OIDC_LOGOUT_URL", raising=False)
    _register(cluster, monkeypatch)
    cluster.refresh_from_db()
    assert cluster.oidc_auth_config["logout_url"] == CONFIG["logout_url"]
    _register(cluster, monkeypatch, ASTROLIFT_CLUSTER_OIDC_LOGOUT_URL="https://idp.example.com/new-logout")
    cluster.refresh_from_db()
    assert cluster.oidc_auth_config["logout_url"] == "https://idp.example.com/new-logout"


def test_invalid_registration_does_not_overwrite_existing_gate(cluster, monkeypatch):
    with pytest.raises(CommandError, match="logout_url"):
        _register(cluster, monkeypatch, ASTROLIFT_CLUSTER_OIDC_LOGOUT_URL="http://idp.example/logout")
    cluster.refresh_from_db()
    assert cluster.oidc_auth_config == CONFIG
