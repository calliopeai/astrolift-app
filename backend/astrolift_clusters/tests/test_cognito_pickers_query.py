"""Tests for the Cognito user-pool picker backend surface (#859).

Covers both resolvers (``astroliftCognitoUserPools`` +
``astroliftCognitoUserPoolClients``) and their dispatch helpers:

* permission gate — denied without ``cluster.update`` (the same
  permission the auth-gate save path requires)
* missing / soft-deleted cluster → empty list (matches the
  cluster_health contract — the card renders empty rather than 404)
* driver / credential failure → empty list (picker degrades to
  free-entry)
* happy path — dispatch dicts map 1:1 onto the GraphQL rows
* dispatch layer converts the driver's Cognito dataclasses to plain
  dicts and wraps driver exceptions in ``ClusterManagementError``
* non-AWS clusters fall through the SDK default (empty list)
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.queries import ClustersQuery
from astrolift_graphql import GUID
from astrolift_identity.models import Organization
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def plugin():
    # bulk_create skips BaseCoreModel.save (numeric version-increment vs
    # CharField), matching the count-query / status-tab suites.
    [row] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="aws",
                slug=f"aws-{uuid.uuid4().hex[:6]}",
                capabilities_manifest={},
                config_schema={},
            ),
        ],
    )
    return row


@pytest.fixture
def cluster(org, plugin):
    return TenantCluster.objects.create(
        organization=org,
        slug=f"c-{uuid.uuid4().hex[:6]}",
        name="dev",
        provider_plugin=plugin,
        provider_config={"region": "us-west-2"},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        ingress_class="alb",
    )


# ─── astroliftCognitoUserPools resolver ──────────────────────────────


def test_cognito_pools_denied_without_cluster_update(cluster, org, permission_resolver):
    """The auth-gate save path is ``cluster.update``-gated; the pool
    picker shares that permission. No grant → denied."""
    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(PermissionDenied):
            ClustersQuery().astrolift_cognito_user_pools(
                _info(),
                cluster_id=GUID(str(cluster.guid)),
            )


def test_cognito_pools_missing_cluster_returns_empty(org, permission_resolver):
    """A non-existent cluster guid returns an empty list, not a raise —
    the card renders empty and the operator can still type values."""
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cognito_user_pools(
            _info(),
            cluster_id=GUID(str(uuid.uuid4())),
        )
    assert result == []


def test_cognito_pools_soft_deleted_cluster_returns_empty(cluster, org, permission_resolver):
    cluster.soft_delete()
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cognito_user_pools(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )
    assert result == []


def test_cognito_pools_driver_failure_returns_empty(cluster, org, permission_resolver, monkeypatch):
    """Driver / credential failure (``ClusterManagementError``) yields
    an empty list so the picker degrades to free-entry."""
    from core import cluster_management

    def _boom(**_kwargs):
        raise cluster_management.ClusterManagementError("no creds")

    monkeypatch.setattr(cluster_management, "cognito_user_pools_dispatch", _boom)
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cognito_user_pools(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )
    assert result == []


def test_cognito_pools_happy_path_maps_rows(cluster, org, permission_resolver, monkeypatch):
    """Dispatch dicts map 1:1 onto ``CognitoUserPoolType`` rows."""
    from core import cluster_management

    def _ok(**_kwargs):
        return [
            {
                "pool_id": "us-west-2_aaa",
                "pool_arn": "arn:aws:cognito-idp:us-west-2:111122223333:userpool/us-west-2_aaa",
                "name": "customers",
                "domain": "customers-auth",
                "region": "us-west-2",
            },
        ]

    monkeypatch.setattr(cluster_management, "cognito_user_pools_dispatch", _ok)
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id)):
        rows = ClustersQuery().astrolift_cognito_user_pools(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
        )
    assert len(rows) == 1
    pool = rows[0]
    assert pool.pool_id == "us-west-2_aaa"
    assert pool.pool_arn == "arn:aws:cognito-idp:us-west-2:111122223333:userpool/us-west-2_aaa"
    assert pool.name == "customers"
    assert pool.domain == "customers-auth"
    assert pool.region == "us-west-2"


# ─── astroliftCognitoUserPoolClients resolver ────────────────────────


def test_cognito_clients_denied_without_cluster_update(cluster, org, permission_resolver):
    with tenant_context(TenantContext(organization_id=org.id)):
        with pytest.raises(PermissionDenied):
            ClustersQuery().astrolift_cognito_user_pool_clients(
                _info(),
                cluster_id=GUID(str(cluster.guid)),
                pool_id="us-west-2_aaa",
            )


def test_cognito_clients_missing_cluster_returns_empty(org, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id)):
        result = ClustersQuery().astrolift_cognito_user_pool_clients(
            _info(),
            cluster_id=GUID(str(uuid.uuid4())),
            pool_id="us-west-2_aaa",
        )
    assert result == []


def test_cognito_clients_happy_path_maps_rows(cluster, org, permission_resolver, monkeypatch):
    from core import cluster_management

    captured = {}

    def _ok(*, cluster, pool_id):  # noqa: ARG001 — cluster bound, pool_id asserted
        captured["pool_id"] = pool_id
        return [
            {"client_id": "c1", "client_name": "web"},
            {"client_id": "c2", "client_name": "mobile"},
        ]

    monkeypatch.setattr(cluster_management, "cognito_user_pool_clients_dispatch", _ok)
    permission_resolver.grant(Permission.CLUSTER_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id)):
        rows = ClustersQuery().astrolift_cognito_user_pool_clients(
            _info(),
            cluster_id=GUID(str(cluster.guid)),
            pool_id="us-west-2_aaa",
        )
    assert captured["pool_id"] == "us-west-2_aaa"
    assert [(c.client_id, c.client_name) for c in rows] == [("c1", "web"), ("c2", "mobile")]


# ─── Dispatch-level tests ────────────────────────────────────────────


def test_cognito_pools_dispatch_converts_dataclasses(cluster, monkeypatch):
    """``cognito_user_pools_dispatch`` calls the driver's
    ``list_cognito_user_pools`` and converts the dataclasses to dicts."""
    from _sdk.cluster import CognitoUserPoolInfo

    from core import cluster_management

    class _FakeDriver:
        def list_cognito_user_pools(self):
            return [
                CognitoUserPoolInfo(
                    pool_id="p1",
                    pool_arn="arn:aws:cognito-idp:us-west-2:1:userpool/p1",
                    name="one",
                    domain="d1",
                    region="us-west-2",
                ),
            ]

    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda c: _FakeDriver())
    out = cluster_management.cognito_user_pools_dispatch(cluster=cluster)
    assert out == [
        {
            "pool_id": "p1",
            "pool_arn": "arn:aws:cognito-idp:us-west-2:1:userpool/p1",
            "name": "one",
            "domain": "d1",
            "region": "us-west-2",
        },
    ]


def test_cognito_pools_dispatch_non_aws_driver_returns_empty(cluster, monkeypatch):
    """A driver that uses the SDK default (non-AWS, returns []) yields an
    empty list — the resolver renders the free-entry fallback."""
    from core import cluster_management

    class _DefaultDriver:
        def list_cognito_user_pools(self):
            return []  # SDK default behavior for non-AWS

    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda c: _DefaultDriver())
    assert cluster_management.cognito_user_pools_dispatch(cluster=cluster) == []


def test_cognito_pools_dispatch_wraps_driver_exception(cluster, monkeypatch):
    from core import cluster_management

    class _BoomDriver:
        def list_cognito_user_pools(self):
            raise RuntimeError("AccessDenied")

    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda c: _BoomDriver())
    with pytest.raises(cluster_management.ClusterManagementError):
        cluster_management.cognito_user_pools_dispatch(cluster=cluster)


def test_cognito_clients_dispatch_threads_pool_id(cluster, monkeypatch):
    """The dispatch passes ``pool_id`` through to the driver and converts
    the result dataclasses to dicts."""
    from _sdk.cluster import CognitoUserPoolClientInfo

    from core import cluster_management

    seen = {}

    class _FakeDriver:
        def list_cognito_user_pool_clients(self, pool_id):
            seen["pool_id"] = pool_id
            return [CognitoUserPoolClientInfo(client_id="c1", client_name="web")]

    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda c: _FakeDriver())
    out = cluster_management.cognito_user_pool_clients_dispatch(cluster=cluster, pool_id="pool-xyz")
    assert seen["pool_id"] == "pool-xyz"
    assert out == [{"client_id": "c1", "client_name": "web"}]
