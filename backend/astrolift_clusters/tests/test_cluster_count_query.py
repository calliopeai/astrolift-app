"""Tests for the astrolift_cluster_count GraphQL query (#315).

Used by the /apps/new wizard's Step 1 to decide whether to render
the connection list or the "Connect a cluster first" empty state.

Boundaries pinned:

* zero clusters -> 0
* one active cluster -> 1
* soft-deleted rows do not count
* inactive rows do not count
* clusters in another org do not count
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.queries import ClustersQuery
from astrolift_identity.models import Organization
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _plugin(slug: str = "local"):
    """Skip BaseCoreModel.save (numeric version vs. CharField) — same
    trick as test_rendered_manifest / the registry test suite."""
    [p] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name=slug,
                slug=slug,
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    return p


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def test_count_is_zero_when_org_has_no_clusters(permission_resolver):
    org = Organization.objects.create(name="Acme", slug="acme-zero")
    permission_resolver.grant(Permission.APP_CREATE)
    with _ctx(org):
        n = ClustersQuery().astrolift_cluster_count(_info())
    assert n == 0


def test_count_includes_active_clusters(permission_resolver):
    org = Organization.objects.create(name="Acme", slug="acme-active")
    plugin = _plugin("active")
    TenantCluster.objects.create(
        organization=org,
        name="c1",
        slug="c1",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    permission_resolver.grant(Permission.APP_CREATE)
    with _ctx(org):
        n = ClustersQuery().astrolift_cluster_count(_info())
    assert n == 1


def test_count_excludes_soft_deleted(permission_resolver):
    org = Organization.objects.create(name="Acme", slug="acme-softdel")
    plugin = _plugin("softdel")
    cluster = TenantCluster.objects.create(
        organization=org,
        name="c1",
        slug="c1",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    cluster.soft_delete()
    permission_resolver.grant(Permission.APP_CREATE)
    with _ctx(org):
        n = ClustersQuery().astrolift_cluster_count(_info())
    assert n == 0


def test_count_excludes_inactive(permission_resolver):
    org = Organization.objects.create(name="Acme", slug="acme-inactive")
    plugin = _plugin("inactive")
    cluster = TenantCluster.objects.create(
        organization=org,
        name="c1",
        slug="c1",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        is_active=False,
    )
    assert cluster.is_active is False
    permission_resolver.grant(Permission.APP_CREATE)
    with _ctx(org):
        n = ClustersQuery().astrolift_cluster_count(_info())
    assert n == 0


def test_count_excludes_other_orgs(permission_resolver):
    org_a = Organization.objects.create(name="A", slug="a-org")
    org_b = Organization.objects.create(name="B", slug="b-org")
    plugin = _plugin("crossorg")
    TenantCluster.objects.create(
        organization=org_b,
        name="b-c1",
        slug="b-c1",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    permission_resolver.grant(Permission.APP_CREATE)
    with _ctx(org_a):
        n = ClustersQuery().astrolift_cluster_count(_info())
    assert n == 0
