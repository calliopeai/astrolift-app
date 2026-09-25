"""Shared (org-NULL) clusters and managed zones are the platform operator's
to write (#1918, #1929).

Every cluster and zone mutation looked rows up with ``Q(org=tenant) |
Q(org__isnull=True)``, so any org holding ``cluster.manage`` or
``provider_plugin.configure`` could rotate a shared cluster's agent key,
decommission it, or delete a shared zone, and any org could create a shared
row with ``organizationScoped: false``. Tenants still deploy onto shared
clusters; only writes to the shared row are the operator's.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_clusters.models import ManagedDomain, ProviderPlugin, TenantCluster
from astrolift_clusters.schema.mutations import (
    ClustersMutation,
    ConfigureProviderPluginInput,
    CreateManagedDomainInput,
    IssueClusterAgentKeyInput,
    RegisterTenantClusterInput,
    SoftDeleteManagedDomainInput,
    UnregisterTenantClusterInput,
)
from astrolift_clusters.tests.test_heartbeat import (  # noqa: F401 (fixture)
    _make_cluster,
    _no_opensearch_profile_index,
)
from astrolift_graphql import GUID
from astrolift_identity.models import Organization
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()

WRITE_PERMS = (
    Permission.CLUSTER_MANAGE,
    Permission.CLUSTER_UNREGISTER,
    Permission.CLUSTER_REGISTER,
    Permission.PROVIDER_PLUGIN_CONFIGURE,
)


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-1918-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def plugin():
    [p] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="k8s",
                slug=f"k8s-1918-{uuid.uuid4().hex[:6]}",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    return p


def _info(user):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user), user=user))


def _tenant_admin():
    return User.objects.create(username=f"admin-{uuid.uuid4().hex[:6]}")


def _operator():
    return User.objects.create(username=f"root-{uuid.uuid4().hex[:6]}", is_superuser=True)


def _denied(result) -> bool:
    return result.ok is False and result.errors[0].code == "PERMISSION_DENIED"


def _grant_all(permission_resolver):
    for perm in WRITE_PERMS:
        permission_resolver.grant(perm)


def test_a_tenant_cannot_rotate_a_shared_clusters_agent_key(org, plugin, permission_resolver):
    _grant_all(permission_resolver)
    shared = _make_cluster(None, plugin)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersMutation().issue_cluster_agent_key(
            _info(_tenant_admin()), IssueClusterAgentKeyInput(cluster_id=GUID(str(shared.guid)))
        )
    assert _denied(result), result
    shared.refresh_from_db()
    assert shared.agent_key_hash == ""


def test_the_operator_can_rotate_a_shared_clusters_agent_key(org, plugin, permission_resolver):
    _grant_all(permission_resolver)
    shared = _make_cluster(None, plugin)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersMutation().issue_cluster_agent_key(
            _info(_operator()), IssueClusterAgentKeyInput(cluster_id=GUID(str(shared.guid)))
        )
    assert result.ok, result


def test_a_tenant_still_manages_its_own_cluster(org, plugin, permission_resolver):
    _grant_all(permission_resolver)
    own = _make_cluster(org, plugin)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersMutation().issue_cluster_agent_key(
            _info(_tenant_admin()), IssueClusterAgentKeyInput(cluster_id=GUID(str(own.guid)))
        )
    assert result.ok, result


def test_a_tenant_cannot_unregister_a_shared_cluster(org, plugin, permission_resolver):
    _grant_all(permission_resolver)
    shared = _make_cluster(None, plugin)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersMutation().unregister_tenant_cluster(
            _info(_tenant_admin()), UnregisterTenantClusterInput(id=GUID(str(shared.guid)))
        )
    assert _denied(result), result
    assert TenantCluster.objects.filter(pk=shared.pk, deleted_at__isnull=True).exists()


def test_a_tenant_cannot_delete_a_shared_zone(org, permission_resolver):
    _grant_all(permission_resolver)
    zone = ManagedDomain.objects.create(
        organization=None, zone=f"z{uuid.uuid4().hex[:6]}.test", dns_driver="route53"
    )
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersMutation().soft_delete_managed_domain(
            _info(_tenant_admin()), SoftDeleteManagedDomainInput(id=GUID(str(zone.guid)))
        )
    assert _denied(result), result
    assert ManagedDomain.objects.filter(pk=zone.pk, deleted_at__isnull=True).exists()


def test_a_tenant_cannot_create_a_shared_cluster_zone_or_plugin_config(org, plugin, permission_resolver):
    _grant_all(permission_resolver)
    admin = _tenant_admin()
    scoped = False
    with tenant_context(TenantContext(organization_id=org.id)):
        cluster = ClustersMutation().register_tenant_cluster(
            _info(admin),
            RegisterTenantClusterInput(
                slug=f"s-{uuid.uuid4().hex[:6]}",
                name="shared",
                provider_plugin_slug=plugin.slug,
                auth_method="kubeconfig",
                organization_scoped=scoped,
            ),
        )
        zone = ClustersMutation().create_managed_domain(
            _info(admin),
            CreateManagedDomainInput(
                zone=f"z{uuid.uuid4().hex[:6]}.test", dns_driver="route53", organization_scoped=scoped
            ),
        )
        config = ClustersMutation().configure_provider_plugin(
            _info(admin),
            ConfigureProviderPluginInput(plugin_slug=plugin.slug, config={}, organization_scoped=scoped),
        )
    assert all(_denied(r) for r in (cluster, zone, config)), (cluster, zone, config)
    assert not TenantCluster.objects.filter(organization__isnull=True, name="shared").exists()
