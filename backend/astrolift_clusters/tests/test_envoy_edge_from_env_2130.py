"""The Envoy edge comes up from the installer's environment (#2130, #2122, #2121).

An install declares the edge with ``ASTROLIFT_CLUSTER_INGRESS_CLASS=envoy`` and
the OIDC vars, the client secret among them and no oauth2-proxy cookie secret.
Registration builds the config from that and starts an additive install of the
edge, so a fresh install or a rebuild needs no hand steps.

The other half matters as much: an existing install on nginx or ALB auth is
left exactly as it is. Nothing here fires for any class but ``envoy``, the
install it starts deletes nothing, and a class flip no longer commits to every
app's repo (which redeployed them all at once).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.utils import timezone

from astrolift_clusters import edge_install
from astrolift_clusters.models import ClusterBootstrapRun, ProviderPlugin, TenantCluster
from astrolift_clusters.schema.mutations import ClustersMutation, UpdateTenantClusterInput
from astrolift_graphql import GUID
from astrolift_identity.models import Organization
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from providers.k8s_native.central_auth import central_auth_component

pytestmark = pytest.mark.django_db

SLUG = "edge-from-env"
DISCOVERY = "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc/.well-known/openid-configuration"
ENVOY_ENV = {
    "ASTROLIFT_CLUSTER_INGRESS_CLASS": "envoy",
    "ASTROLIFT_CLUSTER_OIDC_DISCOVERY_URL": DISCOVERY,
    "ASTROLIFT_CLUSTER_OIDC_CLIENT_ID": "central-client",
    "ASTROLIFT_CLUSTER_OIDC_CLIENT_SECRET": "client-secret-2130",
    "ASTROLIFT_CLUSTER_OIDC_AUTH_PROXY_HOST": "auth.apps.example.net",
}
NGINX_OIDC = {
    "discovery_url": "https://dex.apps.example.net/dex",
    "client_id": "oauth2-proxy",
    "cookie_secret": "cookie-secret-kept-32-bytes-long",
    "client_secret": "nginx-client-secret",
    "auth_proxy_host": "auth.apps.example.net",
}


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture
def aws_plugin(db):
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="aws",
        defaults={"name": "aws", "capabilities_manifest": {}, "config_schema": {}},
    )
    return plugin


@pytest.fixture
def started(monkeypatch):
    """Every workflow start, recorded instead of sent."""
    calls: list[dict] = []

    def _start(name, args, *, workflow_id, task_queue=None):  # noqa: ANN001, ARG001
        calls.append({"name": name, "input": args[0], "workflow_id": workflow_id})

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", _start)
    return calls


@pytest.fixture
def writebacks(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "astrolift_clusters.services.toml_writeback.write_auth_config_for_cluster",
        lambda cluster, actor: calls.append(cluster.slug),
    )
    return calls


def _register(monkeypatch, env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    call_command("register_tenant_cluster", slug=SLUG, plugin_slug="aws")
    return TenantCluster.all_objects.get(slug=SLUG)


def _cluster(**fields):
    org = Organization.objects.create(name="Edge", slug=f"edge-{uuid.uuid4().hex[:8]}")
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="aws", defaults={"name": "aws", "capabilities_manifest": {}, "config_schema": {}}
    )
    return TenantCluster.objects.create(
        organization=org,
        slug=f"edge-{uuid.uuid4().hex[:8]}",
        name="edge",
        provider_plugin=plugin,
        provider_config={},
        region="us-west-2",
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        **fields,
    )


def _update(cluster, **kwargs):
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=None), user=None))
    with tenant_context(TenantContext(organization_id=cluster.organization_id)):
        return ClustersMutation().update_tenant_cluster(
            info, UpdateTenantClusterInput(id=GUID(str(cluster.guid)), **kwargs)
        )


# ---- registration from the installer's environment ------------------------


def test_the_client_secret_alone_completes_the_config(aws_plugin, monkeypatch, started):
    cluster = _register(monkeypatch, ENVOY_ENV)

    assert cluster.ingress_class == "envoy"
    assert cluster.oidc_auth_config["client_secret"] == "client-secret-2130"
    assert cluster.oidc_auth_config["auth_proxy_host"] == "auth.apps.example.net"
    assert "cookie_secret" not in cluster.oidc_auth_config


def test_registering_onto_the_edge_starts_an_additive_edge_install(aws_plugin, monkeypatch, started):
    cluster = _register(monkeypatch, ENVOY_ENV)

    assert len(started) == 1
    run = started[0]
    assert run["name"] == "InstallClusterPrereqsWorkflow"
    assert run["input"].selected_components == ("envoy-gateway",)
    assert run["input"].additive is True
    assert run["input"].cluster_id == cluster.pk
    # Not the operator's workflow id, which is started TERMINATE_IF_RUNNING.
    assert run["workflow_id"] == f"InstallClusterPrereqsWorkflow-{cluster.guid}-edge"


def test_a_restart_after_the_edge_is_installed_starts_nothing(aws_plugin, monkeypatch, started):
    cluster = _register(monkeypatch, ENVOY_ENV)
    ClusterBootstrapRun.objects.create(
        tenant_cluster=cluster,
        status=ClusterBootstrapRun.Status.SUCCEEDED,
        installed_releases=[{"name": "envoy-gateway", "version": "v1.9.1"}],
        started_at=timezone.now(),
        ended_at=timezone.now(),
    )
    started.clear()

    _register(monkeypatch, ENVOY_ENV)

    assert started == []


def test_a_temporal_outage_never_fails_registration(aws_plugin, monkeypatch):
    def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise ConnectionError("temporal down")

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", _boom)

    cluster = _register(monkeypatch, ENVOY_ENV)

    assert cluster.ingress_class == "envoy"


def test_a_client_secret_only_env_keeps_an_operator_set_cookie_secret(aws_plugin, monkeypatch, started):
    cluster = _register(monkeypatch, ENVOY_ENV)
    cluster.oidc_auth_config = {**cluster.oidc_auth_config, "cookie_secret": "operator-cookie"}
    cluster.save(update_fields=["oidc_auth_config"])

    cluster = _register(monkeypatch, ENVOY_ENV)

    assert cluster.oidc_auth_config["cookie_secret"] == "operator-cookie"


def test_a_restart_keeps_an_operator_set_jwks_uri(aws_plugin, monkeypatch, started):
    cluster = _register(monkeypatch, ENVOY_ENV)
    cluster.oidc_auth_config = {**cluster.oidc_auth_config, "jwks_uri": "https://keys.example.net/jwks"}
    cluster.save(update_fields=["oidc_auth_config"])

    cluster = _register(monkeypatch, ENVOY_ENV)

    assert cluster.oidc_auth_config["jwks_uri"] == "https://keys.example.net/jwks"


# ---- an existing install is left exactly as it is -------------------------


@pytest.mark.parametrize("ingress_class", ["nginx", "alb"])
def test_restarting_an_existing_install_changes_nothing(aws_plugin, monkeypatch, started, ingress_class):
    """SteadyMD's shape: a live cluster on nginx (or ALB) auth, and a control
    plane whose environment carries none of the new edge vars."""
    call_command("register_tenant_cluster", slug=SLUG, plugin_slug="aws")
    cluster = TenantCluster.all_objects.get(slug=SLUG)
    cluster.ingress_class = ingress_class
    cluster.oidc_auth_config = dict(NGINX_OIDC)
    cluster.save(update_fields=["ingress_class", "oidc_auth_config"])

    call_command("register_tenant_cluster", slug=SLUG, plugin_slug="aws")

    cluster.refresh_from_db()
    assert cluster.ingress_class == ingress_class
    assert cluster.oidc_auth_config == NGINX_OIDC
    assert started == []


def test_a_complete_config_on_another_class_never_installs_the_edge(started):
    for ingress_class in ("nginx", "alb", "ingress-nginx"):
        assert (
            edge_install.ensure_edge_installed(
                _cluster(ingress_class=ingress_class, oidc_auth_config=NGINX_OIDC)
            )
            is False
        )
    assert started == []


def test_an_incomplete_config_never_installs_the_edge(started):
    cluster = _cluster(ingress_class="envoy", oidc_auth_config={"client_id": "x"})

    assert edge_install.ensure_edge_installed(cluster) is False
    assert started == []


# ---- updateTenantCluster --------------------------------------------------


def test_a_class_flip_no_longer_commits_to_every_app(permission_resolver, writebacks, started):
    """#2122: the write-back commit redeployed every bound app at once."""
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    cluster = _cluster(ingress_class="nginx", oidc_auth_config=NGINX_OIDC)

    result = _update(cluster, ingress_class="envoy")

    assert result.ok is True, result.errors
    assert writebacks == []


def test_a_class_flip_commits_when_the_operator_asks(permission_resolver, writebacks, started):
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    cluster = _cluster(ingress_class="nginx", oidc_auth_config=NGINX_OIDC)

    result = _update(cluster, ingress_class="envoy", sync_manifests=True)

    assert result.ok is True, result.errors
    assert writebacks == [cluster.slug]


def test_changing_the_gate_itself_still_commits(permission_resolver, writebacks, started):
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    cluster = _cluster(ingress_class="nginx", oidc_auth_config=NGINX_OIDC)

    result = _update(cluster, oidc_auth_config={**NGINX_OIDC, "client_id": "rotated"})

    assert result.ok is True, result.errors
    assert writebacks == [cluster.slug]


def test_flipping_onto_the_edge_installs_it(permission_resolver, writebacks, started):
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    cluster = _cluster(ingress_class="nginx", oidc_auth_config=NGINX_OIDC)

    result = _update(cluster, ingress_class="envoy")

    assert result.ok is True, result.errors
    assert [r["input"].additive for r in started] == [True]


# ---- the recipe's oauth2-proxy default (#2121) -----------------------------


@pytest.mark.parametrize(
    ("ingress_class", "on"),
    [("nginx", True), ("ingress-nginx", True), ("", True), ("envoy", False), ("alb", False)],
)
def test_oauth2_proxy_defaults_on_only_where_it_has_something_to_gate(ingress_class, on):
    component = central_auth_component(NGINX_OIDC, ingress_class=ingress_class)

    assert component.default_enabled is on


def test_oauth2_proxy_stays_off_without_a_config():
    assert central_auth_component({}, ingress_class="nginx").default_enabled is False


# ---- the settings page edits the config without holding a secret (#2119) ---


def test_an_update_that_omits_the_secrets_keeps_them(permission_resolver, writebacks, started):
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    cluster = _cluster(ingress_class="nginx", oidc_auth_config=NGINX_OIDC)
    routing_only = {k: v for k, v in NGINX_OIDC.items() if not k.endswith("_secret")}

    result = _update(cluster, oidc_auth_config={**routing_only, "auth_proxy_host": "auth.new.example.net"})

    assert result.ok is True, result.errors
    cluster.refresh_from_db()
    assert cluster.oidc_auth_config["auth_proxy_host"] == "auth.new.example.net"
    assert cluster.oidc_auth_config["client_secret"] == NGINX_OIDC["client_secret"]
    assert cluster.oidc_auth_config["cookie_secret"] == NGINX_OIDC["cookie_secret"]


def test_an_empty_secret_clears_it(permission_resolver, writebacks, started):
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    cluster = _cluster(ingress_class="envoy", oidc_auth_config=NGINX_OIDC)

    result = _update(cluster, oidc_auth_config={**NGINX_OIDC, "cookie_secret": ""})

    assert result.ok is True, result.errors
    cluster.refresh_from_db()
    assert "cookie_secret" not in cluster.oidc_auth_config
    assert cluster.oidc_auth_config["client_secret"] == NGINX_OIDC["client_secret"]


# ---- the recipe card knows what the recipe installed (#2119) --------------


def test_the_plan_marks_a_controller_running_outside_the_recipe(started):
    from astrolift_clusters.schema.types import bootstrap_plan_to_type

    cluster = _cluster(ingress_class="alb", oidc_auth_config={})
    cluster.capabilities = {
        "installed_crds": ["targetgroupbindings.elbv2.k8s.aws"],
        "external_dns": {"installed": True},
        "cert_manager": {"installed": True},
    }
    cluster.save(update_fields=["capabilities"])
    ClusterBootstrapRun.objects.create(
        tenant_cluster=cluster,
        status=ClusterBootstrapRun.Status.SUCCEEDED,
        installed_releases=[{"name": "cert-manager", "version": "v1"}],
        started_at=timezone.now(),
        ended_at=timezone.now(),
    )
    components = [
        SimpleNamespace(
            key=k, title=k, default_enabled=True, rationale="", helm_values={}, requires=[], options=[]
        )
        for k in ("aws-load-balancer-controller", "external-dns", "cert-manager", "metrics-server")
    ]

    plan = {c.key: c for c in bootstrap_plan_to_type(cluster, components).components}

    # Hand-installed: found, and not the recipe's.
    assert plan["aws-load-balancer-controller"].running_outside_recipe is True
    assert plan["external-dns"].running_outside_recipe is True
    # The recipe's own: stays selected, never read as foreign.
    assert plan["cert-manager"].installed_by_recipe is True
    assert plan["cert-manager"].running_outside_recipe is False
    assert plan["metrics-server"].running_outside_recipe is False
