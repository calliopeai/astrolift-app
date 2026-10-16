"""A tenant's cluster Status tab on a shared (org-NULL) cluster (#2418 audit).

#1967 confined a tenant's workload health and system metrics on a shared
cluster to its own namespaces, but three Status reads were left out: the
instant and range Prometheus metrics sum every org's pods, nodes, network
and restarts, and the cluster health card listed the platform namespace's
pods and Warning events.
"""

from __future__ import annotations

import uuid

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.queries import ClustersQuery
from astrolift_graphql import GUID
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

from .test_shared_cluster_reads_1967 import _app_on, _capture, _info, _operator, _org

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, g: None))


@pytest.fixture
def shared():
    [plugin] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="local", slug=f"local-{uuid.uuid4().hex[:6]}", capabilities_manifest={}, config_schema={}
            )
        ]
    )
    return TenantCluster.objects.create(
        organization=None,
        slug=f"shared-{uuid.uuid4().hex[:6]}",
        name="shared",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


@pytest.fixture
def prometheus(shared, monkeypatch):
    shared.provider_config = {"prometheus_endpoint": "http://prom.test:9090"}
    shared.save(update_fields=["provider_config"])
    calls: list[str] = []

    def instant(*, endpoint, query):
        calls.append(query)
        return 1.0

    def range_(*, endpoint, query, start_unix, end_unix, step_seconds):
        calls.append(query)
        return []

    monkeypatch.setattr("astrolift_operations.prometheus_client.query_instant", instant)
    monkeypatch.setattr("astrolift_operations.prometheus_client.query_range", range_)
    return calls


def test_a_tenant_gets_no_cluster_wide_prometheus_sums(shared, prometheus, permission_resolver):
    a = _org("a")
    _app_on(a, shared, "web")
    permission_resolver.grant(Permission.CLUSTER_REGISTER)

    with tenant_context(TenantContext(organization_id=a.id)):
        instant = ClustersQuery().astrolift_cluster_prometheus_metrics(
            _info(), cluster_id=GUID(str(shared.guid))
        )
        ranged = ClustersQuery().astrolift_cluster_prometheus_range_metrics(
            _info(), cluster_id=GUID(str(shared.guid))
        )

    assert instant.available is False and instant.reason == "shared_cluster"
    assert ranged.available is False and ranged.reason == "shared_cluster"
    assert prometheus == []


def test_the_operator_still_reads_the_shared_clusters_prometheus(shared, prometheus, permission_resolver):
    a = _org("a")
    permission_resolver.grant(Permission.CLUSTER_REGISTER)

    with tenant_context(TenantContext(organization_id=a.id)):
        instant = ClustersQuery().astrolift_cluster_prometheus_metrics(
            _info(_operator()), cluster_id=GUID(str(shared.guid))
        )

    assert instant.available is True
    assert prometheus


def test_cluster_health_shows_a_tenant_only_its_own_namespaces(shared, permission_resolver, monkeypatch):
    a, b = _org("a"), _org("b")
    ns_a = _app_on(a, shared, "web")
    _app_on(b, shared, "api")
    calls = _capture(monkeypatch, "cluster_health_dispatch", {"pods": [], "events": []})
    permission_resolver.grant(Permission.CLUSTER_REGISTER)

    with tenant_context(TenantContext(organization_id=a.id)):
        ClustersQuery().astrolift_cluster_health(_info(), cluster_id=GUID(str(shared.guid)))

    assert calls == [{"cluster": shared, "namespaces": [ns_a], "event_limit": 50}]


def test_cluster_health_is_empty_for_a_tenant_with_no_app_there(shared, permission_resolver, monkeypatch):
    a, b = _org("a"), _org("b")
    _app_on(b, shared, "api")
    platform_event = {
        "pods": [{"namespace": "astrolift-system", "phase": "Running", "count": 3}],
        "events": [],
    }
    calls = _capture(monkeypatch, "cluster_health_dispatch", platform_event)
    permission_resolver.grant(Permission.CLUSTER_REGISTER)

    with tenant_context(TenantContext(organization_id=a.id)):
        health = ClustersQuery().astrolift_cluster_health(_info(), cluster_id=GUID(str(shared.guid)))

    assert health.pods == [] and health.events == []
    assert calls == []


def test_the_operator_still_sees_the_shared_clusters_health(shared, permission_resolver, monkeypatch):
    a = _org("a")
    calls = _capture(monkeypatch, "cluster_health_dispatch", {"pods": [], "events": []})
    permission_resolver.grant(Permission.CLUSTER_REGISTER)

    with tenant_context(TenantContext(organization_id=a.id)):
        ClustersQuery().astrolift_cluster_health(_info(_operator()), cluster_id=GUID(str(shared.guid)))

    assert calls == [{"cluster": shared, "namespaces": None, "event_limit": 50}]


def test_a_tenant_counts_only_its_own_apps_on_a_shared_cluster(shared, permission_resolver):
    a, b = _org("a"), _org("b")
    _app_on(a, shared, "web")
    _app_on(b, shared, "api")
    _app_on(b, shared, "worker")
    permission_resolver.grant(Permission.CLUSTER_REGISTER)

    with tenant_context(TenantContext(organization_id=a.id)):
        tenant_count = ClustersQuery().astrolift_app_count_for_cluster(
            _info(), cluster_id=GUID(str(shared.guid))
        )
        operator_count = ClustersQuery().astrolift_app_count_for_cluster(
            _info(_operator()), cluster_id=GUID(str(shared.guid))
        )

    assert tenant_count == 1
    assert operator_count == 3
