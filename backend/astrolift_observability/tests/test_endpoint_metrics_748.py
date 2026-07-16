"""Tests for astroliftAppEndpointMetrics (#748).

Covers:
- Permission gate: APP_READ required
- App not found returns []
- No Prometheus endpoint returns []
- Prometheus error degrades to []
- Happy path: two routes returned, sorted alphabetically
- Routes with zero request rate excluded
- Latency None when histogram series absent for a route
- Latency values converted from seconds to milliseconds
- PromQL builder unit tests: endpoint_request_rate, endpoint_error_rate,
  endpoint_latency_quantile — verify by (http_route) grouping present
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_observability import prom_queries
from astrolift_observability.schema.queries import GoldenSignalsQuery
from astrolift_operations.prometheus_client import PrometheusQueryError
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=SimpleNamespace(user=None)))


def _scaffold(*, prometheus_endpoint: str | None = "http://prom.test:9090"):
    org = Organization.objects.create(name="EpOrg", slug="ep-org")
    team = Team.objects.create(organization=org, name="Eng", slug="ep-eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="ep-demo")
    plugin_obj = ProviderPlugin(
        name="K8s",
        slug="k8s-ep",
        version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin_obj])
    plugin = ProviderPlugin.objects.get(slug="k8s-ep")
    cfg: dict = {}
    if prometheus_endpoint:
        cfg["prometheus_endpoint"] = prometheus_endpoint
    cluster = TenantCluster.objects.create(
        organization=org,
        name="prod",
        slug="prod-ep",
        provider_plugin=plugin,
        provider_config=cfg,
        endpoint="https://k8s.invalid",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="EpApp",
        slug="ep-app",
        provisioning_status="ready",
    )
    AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url="https://ep-app.example.com",
    )
    return org, app


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


# ---- PromQL builder unit tests (no DB) ------------------------------


def test_endpoint_request_rate_query_groups_by_route():
    plan = prom_queries.build_endpoint_request_rate_query(
        app_slug="myapp", environment_name="prod", range_seconds=3600
    )
    assert "by (http_route)" in plan.promql
    assert "http_requests_total" in plan.promql
    assert 'app="myapp"' in plan.promql


def test_endpoint_error_rate_query_groups_by_route():
    plan = prom_queries.build_endpoint_error_rate_query(
        app_slug="myapp", environment_name="prod", range_seconds=3600
    )
    assert "by (http_route)" in plan.promql
    assert "5.." in plan.promql
    assert "clamp_min" in plan.promql


def test_endpoint_latency_query_groups_by_route_and_le():
    plan = prom_queries.build_endpoint_latency_quantile_query(
        app_slug="myapp", environment_name="prod", range_seconds=3600, quantile=0.99
    )
    assert "by (http_route, le)" in plan.promql
    assert "histogram_quantile(0.99" in plan.promql
    assert "http_request_duration_seconds_bucket" in plan.promql


# ---- resolver gate + empty-state ------------------------------------


def test_endpoint_metrics_requires_app_read(permission_resolver):
    org, app = _scaffold()
    with _ctx(org):
        with pytest.raises(PermissionDenied):
            GoldenSignalsQuery().astrolift_app_endpoint_metrics(_info(), app_slug=app.slug)


def test_endpoint_metrics_app_not_found(permission_resolver):
    org, _ = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        result = GoldenSignalsQuery().astrolift_app_endpoint_metrics(_info(), app_slug="no-such-app")
    assert result == []


def test_endpoint_metrics_no_prom_endpoint(permission_resolver):
    org, app = _scaffold(prometheus_endpoint=None)
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        result = GoldenSignalsQuery().astrolift_app_endpoint_metrics(_info(), app_slug=app.slug)
    assert result == []


def test_endpoint_metrics_prom_error_degrades(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    with (
        _ctx(org),
        patch(
            "astrolift_observability.prom_client.query_range_series",
            side_effect=PrometheusQueryError("prom down"),
        ),
    ):
        result = GoldenSignalsQuery().astrolift_app_endpoint_metrics(_info(), app_slug=app.slug)
    assert result == []


# ---- happy path -----------------------------------------------------


def _mock_query_range_series(responses: dict[str, list]):
    """Return a side_effect function that dispatches by PromQL content."""
    calls: list[str] = []

    def _side_effect(*, endpoint, promql, start_unix, end_unix, step_seconds, label_key=None):
        calls.append(promql)
        # Match by characteristic substring
        for key, series in responses.items():
            if key in promql:
                return series
        return []

    return _side_effect, calls


def test_endpoint_metrics_happy_path(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)

    now_ts = 1_700_000_000
    # rate: two routes
    rate_series = [
        ("GET /users", [(now_ts - 60, 10.0), (now_ts, 12.0)]),
        ("POST /orders", [(now_ts - 60, 5.0), (now_ts, 6.0)]),
    ]
    # errors: only /users has errors
    err_series = [
        ("GET /users", [(now_ts, 0.05)]),
    ]
    # p50 latency (seconds in Prometheus, ms returned by resolver)
    lat_p50 = [
        ("GET /users", [(now_ts, 0.02)]),  # 20ms
        ("POST /orders", [(now_ts, 0.01)]),  # 10ms
    ]
    lat_p90 = [
        ("GET /users", [(now_ts, 0.05)]),  # 50ms
        ("POST /orders", [(now_ts, 0.03)]),  # 30ms
    ]
    lat_p99 = [
        ("GET /users", [(now_ts, 0.1)]),  # 100ms
        ("POST /orders", [(now_ts, 0.08)]),  # 80ms
    ]

    def _qs(*, endpoint, promql, start_unix, end_unix, step_seconds, label_key=None):
        if "5.." in promql:
            return err_series
        if "0.99" in promql:
            return lat_p99
        if "0.9," in promql or "0.9)" in promql:
            return lat_p90
        if "0.5" in promql:
            return lat_p50
        # request rate
        return rate_series

    with (
        _ctx(org),
        patch(
            "astrolift_observability.prom_client.query_range_series",
            side_effect=_qs,
        ),
    ):
        result = GoldenSignalsQuery().astrolift_app_endpoint_metrics(_info(), app_slug=app.slug)

    # Routes returned alphabetically
    assert len(result) == 2
    assert result[0].route == "GET /users"
    assert result[1].route == "POST /orders"

    users = result[0]
    assert users.request_rate == pytest.approx(12.0)
    assert users.error_rate_ratio == pytest.approx(0.05)
    assert users.p50_ms == pytest.approx(20.0)
    assert users.p90_ms == pytest.approx(50.0)
    assert users.p99_ms == pytest.approx(100.0)

    orders = result[1]
    assert orders.request_rate == pytest.approx(6.0)
    assert orders.error_rate_ratio == pytest.approx(0.0)
    assert orders.p50_ms == pytest.approx(10.0)


def test_endpoint_metrics_zero_rate_routes_excluded(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    now_ts = 1_700_000_000
    rate_series = [
        ("GET /active", [(now_ts, 5.0)]),
        ("GET /stale", [(now_ts, 0.0)]),  # zero rate
    ]

    def _qs(*, endpoint, promql, start_unix, end_unix, step_seconds, label_key=None):
        if "5.." in promql or "histogram" in promql or "quantile" in promql:
            return []
        return rate_series

    with (
        _ctx(org),
        patch(
            "astrolift_observability.prom_client.query_range_series",
            side_effect=_qs,
        ),
    ):
        result = GoldenSignalsQuery().astrolift_app_endpoint_metrics(_info(), app_slug=app.slug)

    routes = [m.route for m in result]
    assert "GET /active" in routes
    assert "GET /stale" not in routes


def test_endpoint_metrics_none_latency_when_no_histogram(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    now_ts = 1_700_000_000
    rate_series = [("GET /fast", [(now_ts, 3.0)])]

    def _qs(*, endpoint, promql, start_unix, end_unix, step_seconds, label_key=None):
        if "histogram" in promql or "quantile" in promql or "bucket" in promql:
            return []
        if "5.." in promql:
            return []
        return rate_series

    with (
        _ctx(org),
        patch(
            "astrolift_observability.prom_client.query_range_series",
            side_effect=_qs,
        ),
    ):
        result = GoldenSignalsQuery().astrolift_app_endpoint_metrics(_info(), app_slug=app.slug)

    assert len(result) == 1
    assert result[0].p50_ms is None
    assert result[0].p90_ms is None
    assert result[0].p99_ms is None
