"""Tests for ``astroliftAppMetrics(app_slug, time_range)``.

Until Prometheus ingestion is wired into the platform, the resolver
returns deterministic synthetic data (hash(app.guid)-seeded) plus
a real ``deploy_count`` aggregated from ``Deployment`` rows in the
window. Tests cover:

* Accepts the five documented time_range values; rejects others.
* Returns None for an unknown app.
* deploy_count reflects actual rows in the window (and zero outside).
* time_series length matches the bucket count for the window.
* Output is deterministic for the same app+window — re-running the
  resolver yields identical numbers.
* Permission gate is APP_READ_METRICS.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_operations.schema.queries import OperationsQuery
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=SimpleNamespace(user=None)))


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-app",
        provisioning_status="ready",
    )
    return org, app


def _tenant(org):
    return tenant_context(TenantContext(organization_id=org.id))


def test_returns_none_for_unknown_app(permission_resolver):
    org, _ = _scaffold()
    permission_resolver.grant(Permission.APP_READ_METRICS)
    with _tenant(org):
        result = OperationsQuery().astrolift_app_metrics(_info(), app_slug="does-not-exist")
    assert result is None


def test_default_time_range_is_1h(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ_METRICS)
    with _tenant(org):
        result = OperationsQuery().astrolift_app_metrics(_info(), app_slug=app.slug)
    assert result is not None
    assert result.time_range == "1h"
    assert len(result.time_series) == 12  # _TIME_RANGE_BUCKETS["1h"]
    assert result.source == "synthetic"


@pytest.mark.parametrize(
    ("time_range", "expected_bucket_count"),
    [
        ("5m", 12),
        ("1h", 12),
        ("24h", 24),
        ("7d", 28),
        ("30d", 30),
    ],
)
def test_each_time_range_yields_expected_bucket_count(time_range, expected_bucket_count, permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ_METRICS)
    with _tenant(org):
        result = OperationsQuery().astrolift_app_metrics(_info(), app_slug=app.slug, time_range=time_range)
    assert result is not None
    assert result.time_range == time_range
    assert len(result.time_series) == expected_bucket_count


def test_unknown_time_range_raises(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ_METRICS)
    with _tenant(org):
        with pytest.raises(ValueError):
            OperationsQuery().astrolift_app_metrics(_info(), app_slug=app.slug, time_range="bogus")


def test_deploy_count_reflects_real_deployments_in_window(permission_resolver):
    from astrolift_clusters.models import ProviderPlugin, TenantCluster

    org, app = _scaffold()
    plugin = ProviderPlugin(
        name="Test", slug="t-metrics", version="0.0.1", capabilities_manifest={}, config_schema={}
    )
    ProviderPlugin.objects.bulk_create([plugin])
    plugin = ProviderPlugin.objects.get(slug="t-metrics")
    cluster = TenantCluster.objects.create(
        organization=org,
        name="dev",
        slug="dev-metrics",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://dev.cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url="https://hello.example.com",
    )
    permission_resolver.grant(Permission.APP_READ_METRICS)

    # Three deploys inside the 1h window
    for _ in range(3):
        Deployment.objects.create(
            registered_app=app,
            app_environment=env,
            trigger_kind="manual",
            status=Deployment.Status.RUNNING.value,
            image_tag="v1",
        )

    # One deploy comfortably outside the 1h window (created 2h ago)
    old = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind="manual",
        status=Deployment.Status.RUNNING.value,
        image_tag="v0",
    )
    Deployment.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=2))

    with _tenant(org):
        result = OperationsQuery().astrolift_app_metrics(_info(), app_slug=app.slug, time_range="1h")
    assert result is not None
    assert result.deploy_count == 3

    with _tenant(org):
        result_24h = OperationsQuery().astrolift_app_metrics(_info(), app_slug=app.slug, time_range="24h")
    assert result_24h is not None
    # The 24h window must include the older deploy too.
    assert result_24h.deploy_count == 4


def test_output_is_deterministic_for_same_app(permission_resolver):
    """Same app + same window → same numbers. Required for the UI to
    show stable values across refreshes until real ingestion lands."""
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ_METRICS)
    with _tenant(org):
        a = OperationsQuery().astrolift_app_metrics(_info(), app_slug=app.slug, time_range="1h")
        b = OperationsQuery().astrolift_app_metrics(_info(), app_slug=app.slug, time_range="1h")
    assert a is not None and b is not None
    assert a.request_rate == b.request_rate
    assert a.p95_latency_ms == b.p95_latency_ms
    assert [p.request_rate for p in a.time_series] == [p.request_rate for p in b.time_series]


def test_no_permission_denied(permission_resolver):
    """Without ``app.read_metrics`` the resolver refuses."""
    from core.permissions import PermissionDenied

    org, app = _scaffold()
    with _tenant(org):
        with pytest.raises(PermissionDenied):
            OperationsQuery().astrolift_app_metrics(_info(), app_slug=app.slug)


def test_latencies_are_sorted_p50_le_p95_le_p99(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ_METRICS)
    with _tenant(org):
        result = OperationsQuery().astrolift_app_metrics(_info(), app_slug=app.slug, time_range="1h")
    assert result is not None
    assert result.p50_latency_ms <= result.p95_latency_ms
    assert result.p95_latency_ms <= result.p99_latency_ms
