"""Unsupported TLS fronts fail before a cluster, workflow or route is changed."""

from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from astrolift_clusters.edge_install import ensure_edge_installed
from astrolift_clusters.models import TenantCluster
from astrolift_clusters.schema.mutations import (
    ClustersMutation,
    InstallClusterPrereqsInputType,
    RegisterTenantClusterInput,
    UpdateTenantClusterInput,
)
from astrolift_graphql import GUID
from astrolift_workflows.activities.install_prereqs import _install_cluster_prereqs_sync
from core.app_deploy import AppDeployError, envoy_edge_routes
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["azure", "gcp", "k8s_native"])
def unsupported_cluster(cluster, request):
    plugin = cluster.provider_plugin
    plugin.slug = request.param
    plugin.save()
    return cluster


def test_class_update_refuses_without_saving_or_starting(
    unsupported_cluster, permission_resolver, monkeypatch
):
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    starts = []
    monkeypatch.setattr("astrolift_workflows.client.start_workflow", lambda *a, **k: starts.append(a))
    cluster = unsupported_cluster
    old_version = cluster.version
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=None), user=None))
    with tenant_context(TenantContext(organization_id=cluster.organization_id)):
        result = ClustersMutation().update_tenant_cluster(
            info, UpdateTenantClusterInput(id=GUID(str(cluster.guid)), ingress_class="envoy")
        )
    assert not result.ok
    assert "no supported TLS front" in result.errors[0].message
    cluster.refresh_from_db()
    assert cluster.ingress_class != "envoy"
    assert cluster.version == old_version
    assert starts == []


def test_graphql_registration_refuses_envoy_but_preserves_existing_ingress(
    unsupported_cluster, permission_resolver
):
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    cluster = unsupported_cluster
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=None), user=None))
    count = TenantCluster.all_objects.count()
    with tenant_context(TenantContext(organization_id=cluster.organization_id)):
        result = ClustersMutation().register_tenant_cluster(
            info,
            RegisterTenantClusterInput(
                slug="unsupported-envoy-graphql",
                name="Unsupported edge",
                provider_plugin_slug=cluster.provider_plugin.slug,
                auth_method="kubeconfig",
                ingress_class="envoy",
            ),
        )
        assert not result.ok
        assert "no supported TLS front" in result.errors[0].message
        assert TenantCluster.all_objects.count() == count

        result = ClustersMutation().register_tenant_cluster(
            info,
            RegisterTenantClusterInput(
                slug="supported-existing-ingress",
                name="Existing ingress",
                provider_plugin_slug=cluster.provider_plugin.slug,
                auth_method="kubeconfig",
                ingress_class="nginx",
            ),
        )
    assert result.ok
    registered = TenantCluster.objects.get(slug="supported-existing-ingress")
    assert registered.organization_id == cluster.organization_id
    assert registered.ingress_class == "nginx"
    assert TenantCluster.all_objects.count() == count + 1


def test_install_refuses_before_driver_creation(unsupported_cluster, monkeypatch):
    calls = []
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda c: calls.append(c))
    with pytest.raises(AppDeployError, match="no supported TLS front"):
        _install_cluster_prereqs_sync(unsupported_cluster.pk, ["envoy-gateway"], {}, additive=True)
    assert calls == []


def test_install_mutation_refuses_before_enqueuing(unsupported_cluster, permission_resolver, monkeypatch):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    calls = []
    monkeypatch.setattr("astrolift_clusters.schema.mutations.start_workflow", lambda *a, **k: calls.append(a))
    cluster = unsupported_cluster
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=None), user=None))
    with tenant_context(TenantContext(organization_id=cluster.organization_id)):
        result = ClustersMutation().install_cluster_prereqs(
            info,
            InstallClusterPrereqsInputType(
                cluster_id=GUID(str(cluster.guid)), selected_components=["envoy-gateway"]
            ),
        )
    assert not result.ok
    assert "no supported TLS front" in result.errors[0].message
    assert calls == []


def test_existing_unsupported_configuration_never_starts_or_renders(unsupported_cluster, app, monkeypatch):
    cluster = unsupported_cluster
    cluster.ingress_class = "envoy"
    cluster.oidc_auth_config = {
        "discovery_url": "https://idp.example/.well-known/openid-configuration",
        "client_id": "client",
        "auth_proxy_host": "auth.apps.example",
    }
    calls = []
    monkeypatch.setattr("astrolift_workflows.client.start_workflow", lambda *a, **k: calls.append(a))
    assert not ensure_edge_installed(cluster)
    with pytest.raises(AppDeployError, match="no supported TLS front"):
        envoy_edge_routes(
            app,
            namespace="app",
            workloads={"web": (["app.example"], 8080)},
            cluster=cluster,
            paused=False,
        )
    assert calls == []


@pytest.mark.parametrize("plugin", ["azure", "gcp", "k8s_native"])
def test_registration_refuses_before_creating_cluster(plugin):
    count = TenantCluster.all_objects.count()
    with pytest.raises(CommandError, match="no supported TLS front"):
        call_command(
            "register_tenant_cluster", slug="unsupported-envoy", plugin_slug=plugin, ingress_class="envoy"
        )
    assert TenantCluster.all_objects.count() == count
