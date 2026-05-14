"""Tests for bringClusterIntoManagement + refreshClusterManagement (#316).

The Temporal client is stubbed via ``ASTROLIFT_TEMPORAL_ENABLED=False``
so the mutations write the lifecycle transition and return without
contacting a real worker. The activity-level + workflow-level tests
in ``astrolift_workflows.tests.test_bring_cluster_into_management``
cover the workflow body.

Boundaries pinned here:
* permission gate denies callers without ``cluster.manage``
* lifecycle flips to ``managing`` synchronously on the bring path
* unknown cluster id returns NOT_FOUND
* inactive cluster returns PRECONDITION
* re-firing on a managing row no-ops the lifecycle write
* refresh accepts an already-managed cluster
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.mutations import (
    BringClusterIntoManagementInputType,
    ClustersMutation,
    RefreshClusterManagementInputType,
)
from astrolift_graphql import GUID
from astrolift_identity.models import Organization
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_temporal(settings):
    """Mutations call start_workflow which talks to Temporal — bypass
    in tests; the workflow-level tests cover the real path."""
    settings.ASTROLIFT_TEMPORAL_ENABLED = False


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    """Suppress the User -> Profile -> OpenSearch indexing chain that
    fires on every Organization/User create. Same pattern the
    lifecycle conftest uses."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def plugin():
    [p] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="k8s",
                slug="k8s_native",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    return p


@pytest.fixture
def cluster(org, plugin):
    return TenantCluster.objects.create(
        organization=org,
        slug=f"c-{uuid.uuid4().hex[:6]}",
        name="dev",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.REGISTERED.value,
    )


def _info(org):
    """Resolver-shaped Info object. Anonymous user — the permission
    resolver fixture grants the permission directly."""
    request = SimpleNamespace(user=None)
    return SimpleNamespace(context=SimpleNamespace(request=request, user=None))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


# ---- bringClusterIntoManagement ----------------------------------


def test_bring_denied_without_cluster_manage_permission(cluster, org, permission_resolver):
    # Resolver default = deny; don't grant. mutation_audit catches the
    # PermissionDenied and surfaces it as a MutationResult failure.
    with _ctx(org):
        result = ClustersMutation().bring_cluster_into_management(
            _info(org),
            BringClusterIntoManagementInputType(cluster_id=GUID(str(cluster.guid))),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "PERMISSION_DENIED"
    cluster.refresh_from_db()
    # Permission denial MUST NOT flip the lifecycle.
    assert cluster.lifecycle == TenantCluster.Lifecycle.REGISTERED.value


def test_bring_flips_lifecycle_to_managing(cluster, org, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org):
        result = ClustersMutation().bring_cluster_into_management(
            _info(org),
            BringClusterIntoManagementInputType(cluster_id=GUID(str(cluster.guid))),
        )
    assert result.ok is True
    cluster.refresh_from_db()
    assert cluster.lifecycle == TenantCluster.Lifecycle.MANAGING.value


def test_bring_returns_not_found_on_unknown_guid(org, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    fake = GUID(str(uuid.uuid4()))
    with _ctx(org):
        result = ClustersMutation().bring_cluster_into_management(
            _info(org),
            BringClusterIntoManagementInputType(cluster_id=fake),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "NOT_FOUND"


def test_bring_rejects_inactive_cluster(cluster, org, permission_resolver):
    cluster.is_active = False
    cluster.save(update_fields=["is_active"])
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org):
        result = ClustersMutation().bring_cluster_into_management(
            _info(org),
            BringClusterIntoManagementInputType(cluster_id=GUID(str(cluster.guid))),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "PRECONDITION"


def test_bring_is_idempotent_on_already_managing_row(cluster, org, permission_resolver):
    cluster.lifecycle = TenantCluster.Lifecycle.MANAGING.value
    cluster.save(update_fields=["lifecycle"])
    before = cluster.updated_at
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org):
        result = ClustersMutation().bring_cluster_into_management(
            _info(org),
            BringClusterIntoManagementInputType(cluster_id=GUID(str(cluster.guid))),
        )
    assert result.ok is True
    cluster.refresh_from_db()
    # No write -> updated_at unchanged
    assert cluster.updated_at == before


# ---- refreshClusterManagement ------------------------------------


def test_refresh_accepts_managed_cluster(cluster, org, permission_resolver):
    cluster.lifecycle = TenantCluster.Lifecycle.MANAGED.value
    cluster.save(update_fields=["lifecycle"])
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org):
        result = ClustersMutation().refresh_cluster_management(
            _info(org),
            RefreshClusterManagementInputType(cluster_id=GUID(str(cluster.guid)), force_preflight=False),
        )
    assert result.ok is True
    cluster.refresh_from_db()
    assert cluster.lifecycle == TenantCluster.Lifecycle.MANAGING.value


def test_refresh_denied_without_permission(cluster, org, permission_resolver):
    with _ctx(org):
        result = ClustersMutation().refresh_cluster_management(
            _info(org),
            RefreshClusterManagementInputType(cluster_id=GUID(str(cluster.guid))),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "PERMISSION_DENIED"


def test_refresh_returns_not_found(org, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org):
        result = ClustersMutation().refresh_cluster_management(
            _info(org),
            RefreshClusterManagementInputType(cluster_id=GUID(str(uuid.uuid4()))),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "NOT_FOUND"
