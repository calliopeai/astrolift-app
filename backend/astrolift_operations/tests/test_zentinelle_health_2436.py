# ruff: noqa: F811 (the #1888 fixtures are imported, then requested by name)
"""Zentinelle health reads for the /secure/zentinelle health card (#2436).

Zentinelle is faked at ``requests.request`` with the install channel's
``/health`` read (calliopeai/zentinelle, ``astrolift/health``); RBAC is real
roles on the #1888 two-team world, so the narrowing is the resolver's.
"""

from __future__ import annotations

import dataclasses
import uuid

import pytest
from django.core.cache import cache
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_operations import zentinelle_connect, zentinelle_reads
from astrolift_operations.models import ZentinelleClusterGateway, ZentinelleConnection
from astrolift_operations.schema.queries import OperationsQuery
from astrolift_operations.schema.types import zentinelle_connection_to_type
from astrolift_operations.tests.test_zentinelle_reads_1888 import (  # noqa: F401 (fixtures)
    FakeResponse,
    FakeZentinelle,
    _connect,
    _grant,
    _no_opensearch_profile_index,
    world,
)
from core.permissions import Permission, PermissionDenied
from core.tests.utils.scope_world import as_tenant

pytestmark = pytest.mark.django_db

SEEN = "2026-10-17T12:00:00+00:00"


class FakeHealthZentinelle(FakeZentinelle):
    def __init__(self, credential: str) -> None:
        super().__init__(credential)
        self.server_version = "1.5.0"
        self.clusters: list[str] = []

    def __call__(self, method, url, **kwargs):
        reply = super().__call__(method, url, **kwargs)
        call = self.calls[-1]
        if call.path != "/health" or reply.status_code != 404 or call.path in self.status:
            return reply
        agents = call.query.get("deployment_id") or ["medops", "platform"]
        return FakeResponse(
            200,
            {
                "server_version": self.server_version,
                "window_hours": 24,
                "blocks": 3 * len(agents),
                "open_approvals": len(agents),
                "budgets": [
                    {
                        "id": "pol-1",
                        "name": "org budget",
                        "scope_type": "organization",
                        "limit_usd": 100.0,
                        "spend_usd": 85.5,
                        "percent": 85.5,
                        "level": "threshold",
                    }
                ],
                "clusters": [
                    {
                        "cluster_id": cluster_id,
                        "status": "active",
                        "health": "healthy",
                        "gateway_version": "0.9.1",
                        "last_seen_at": SEEN,
                    }
                    for cluster_id in self.clusters
                ]
                + [
                    {
                        "cluster_id": "never-reported",
                        "status": "stale",
                        "health": "unknown",
                        "gateway_version": "",
                        "last_seen_at": None,
                    }
                ],
            },
        )

    def health_calls(self):
        return [c for c in self.calls if c.path == "/health"]


@pytest.fixture
def zentinelle(world, monkeypatch) -> FakeHealthZentinelle:
    fake = FakeHealthZentinelle(_connect(world))
    monkeypatch.setattr(zentinelle_connect.requests, "request", fake)
    return fake


def _health(world):
    with as_tenant(world, world.user):
        return OperationsQuery().astrolift_zentinelle_health(world.info)


def test_an_org_wide_viewer_reads_the_whole_tenants_health(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_STATUS_VIEW])

    result = _health(world)

    [call] = zentinelle.health_calls()
    assert call.query["tenant_id"] == ["tenant-a"] and "deployment_id" not in call.query
    assert (result.available, result.window_hours, result.blocks, result.open_approvals) == (True, 24, 6, 2)
    assert not hasattr(result, "budgets")


def test_the_answer_is_cached_not_asked_again(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_STATUS_VIEW])
    _health(world)
    _health(world)
    assert len(zentinelle.health_calls()) == 1


def test_a_project_viewer_reads_only_its_projects_agents(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_STATUS_VIEW], kind="PROJECT")

    result = _health(world)

    [call] = zentinelle.health_calls()
    assert call.query["deployment_id"] == ["medops"]
    assert (result.blocks, result.open_approvals) == (3, 1)


def test_health_needs_status_view(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_USAGE_VIEW])
    with pytest.raises(PermissionDenied):
        _health(world)


def test_an_older_zentinelle_reads_as_not_available_not_as_an_error(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_STATUS_VIEW])
    zentinelle.status["/health"] = 404

    result = _health(world)
    _health(world)

    assert result.available is False
    assert (result.blocks, result.open_approvals, result.window_hours) == (None, None, None)
    assert len(zentinelle.health_calls()) == 1


def test_a_failing_zentinelle_is_an_error_and_not_cached(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_STATUS_VIEW])
    zentinelle.status["/health"] = 500
    for _ in range(2):
        with pytest.raises(Exception) as raised:
            _health(world)
        assert raised.value.extensions["code"] == "INTERNAL"
    assert len(zentinelle.health_calls()) == 2


def test_not_connected_is_said(world, monkeypatch):
    monkeypatch.setattr(zentinelle_connect.requests, "request", lambda *a, **k: pytest.fail("called"))
    _grant(world, [Permission.ZENTINELLE_STATUS_VIEW])
    with pytest.raises(Exception) as raised:
        _health(world)
    assert raised.value.extensions["code"] == "PRECONDITION"


# ---- the connection's server version and its gateways' version and last seen ----


def _gateway(world, connection):
    [plugin] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="k8s", slug=f"k8s-{uuid.uuid4().hex[:6]}", capabilities_manifest={}, config_schema={}
            )
        ]
    )
    cluster = TenantCluster.objects.create(
        organization=world.org,
        slug=f"dev-{uuid.uuid4().hex[:6]}",
        name="dev",
        provider_plugin=plugin,
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
    )
    return ZentinelleClusterGateway.objects.create(
        connection=connection,
        cluster=cluster,
        zentinelle_cluster_id=str(cluster.guid),
        registered_at=timezone.now(),
    )


def test_the_connection_carries_versions_and_last_seen(world, zentinelle):
    connection = ZentinelleConnection.objects.get(organization=world.org)
    reported = _gateway(world, connection)
    unknown = _gateway(world, connection)
    zentinelle.clusters = [reported.zentinelle_cluster_id]

    result = zentinelle_connection_to_type(connection)
    by_id = {c.zentinelle_cluster_id: c for c in result.clusters}

    assert result.server_version() == "1.5.0"
    assert by_id[reported.zentinelle_cluster_id].gateway_version() == "0.9.1"
    assert by_id[reported.zentinelle_cluster_id].last_seen_at().isoformat() == SEEN
    # Zentinelle does not know this one: nothing to say, rather than a guess.
    assert by_id[unknown.zentinelle_cluster_id].gateway_version() is None
    assert by_id[unknown.zentinelle_cluster_id].last_seen_at() is None
    # One call for the whole response, install-wide.
    [call] = zentinelle.health_calls()
    assert "deployment_id" not in call.query


def test_the_connection_reads_without_them_from_an_older_or_failing_zentinelle(world, zentinelle):
    connection = ZentinelleConnection.objects.get(organization=world.org)
    gateway = _gateway(world, connection)
    zentinelle.clusters = [gateway.zentinelle_cluster_id]
    for status in (404, 500):
        zentinelle.status["/health"] = status
        result = zentinelle_connection_to_type(connection)
        assert result.server_version() is None
        assert result.clusters[0].gateway_version() is None
        assert result.clusters[0].last_seen_at() is None
        assert result.status == "connected"


def test_an_empty_server_version_reads_as_unknown(world, zentinelle):
    zentinelle.server_version = ""
    connection = ZentinelleConnection.objects.get(organization=world.org)
    assert zentinelle_connection_to_type(connection).server_version() is None


def test_a_disconnected_connection_never_asks(world, monkeypatch):
    monkeypatch.setattr(zentinelle_connect.requests, "request", lambda *a, **k: pytest.fail("called"))
    _connect(world, status=ZentinelleConnection.Status.DISCONNECTED)
    connection = ZentinelleConnection.objects.get(organization=world.org)
    assert zentinelle_connection_to_type(connection).server_version() is None


def test_health_cache_contains_only_typed_nonmonetary_observations(world, zentinelle):
    _grant(world, [Permission.ZENTINELLE_STATUS_VIEW])
    connection = ZentinelleConnection.objects.get(organization=world.org)
    legacy_key = f"zentinelle-health:{connection.guid}:*"
    legacy = {"body": {"budgets": [{"limit_usd": 999, "spend_usd": 777}], "blocks": 999}}
    cache.set(legacy_key, legacy, zentinelle_reads.HEALTH_CACHE_SECONDS)

    result = _health(world)

    assert result.blocks == 6 and len(zentinelle.health_calls()) == 1
    snapshot = cache.get(f"zentinelle-health-v2:{connection.guid}:*")
    assert isinstance(snapshot, zentinelle_reads.Health)
    assert set(dataclasses.asdict(snapshot)) == {
        "available",
        "server_version",
        "window_hours",
        "blocks",
        "open_approvals",
        "clusters",
    }
    assert snapshot.server_version == "1.5.0" and snapshot.blocks == 6
    assert not hasattr(zentinelle_reads, "Budget")
    assert cache.get(legacy_key) == legacy  # Neither adopt nor rewrite the earlier raw answer.
    _health(world)
    assert len(zentinelle.health_calls()) == 1


def test_health_schema_has_no_budget_type_or_field():
    from config.schema import schema

    sdl = schema.as_str()
    assert "type AstroliftZentinelleBudget" not in sdl
    health_type = sdl.split("type AstroliftZentinelleHealth {", 1)[1].split("}", 1)[0]
    assert "budgets" not in health_type
    for field in ("available:", "windowHours:", "blocks:", "openApprovals:"):
        assert field in health_type
