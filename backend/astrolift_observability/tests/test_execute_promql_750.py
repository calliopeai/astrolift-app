"""Tests for the astroliftExecutePromql query (#750).

Covers:
- Permission gate: APP_READ required
- App not found returns ok=False
- No Prometheus endpoint returns ok=False
- Empty / blank query returns ok=False
- Bad step_seconds (<=0) returns ok=False
- end_unix <= start_unix returns ok=False
- Happy path: rows returned as PromqlSeries with metric_labels + values
- PrometheusError degrades to ok=False (no exception raised)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_observability.schema.queries import GoldenSignalsQuery
from astrolift_operations.prometheus_client import PrometheusQueryError
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=SimpleNamespace(user=None)))


def _scaffold(
    *, prometheus_endpoint: str | None = "http://prom.test:9090"
) -> tuple[Organization, RegisteredApp]:
    org = Organization.objects.create(name="PromOrg", slug="prom-exec-org")
    team = Team.objects.create(organization=org, name="Eng", slug="prom-exec-eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="prom-exec-demo")
    plugin_obj = ProviderPlugin(
        name="K8s",
        slug="k8s-prom-exec",
        version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin_obj])
    plugin = ProviderPlugin.objects.get(slug="k8s-prom-exec")
    cfg: dict = {}
    if prometheus_endpoint:
        cfg["prometheus_endpoint"] = prometheus_endpoint
    cluster = TenantCluster.objects.create(
        organization=org,
        name="prod",
        slug="prod-prom-exec",
        provider_plugin=plugin,
        provider_config=cfg,
        endpoint="https://k8s.invalid",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="PromApp",
        slug="prom-exec-app",
        provisioning_status="ready",
    )
    AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url="https://prom-exec-app.example.com",
    )
    return org, app


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


_START = 1_700_000_000
_END = _START + 3600
_STEP = 60


def _fake_row(labels: dict, values: list) -> MagicMock:
    row = MagicMock()
    row.metric_labels = labels
    row.values = [tuple(pair) for pair in values]
    return row


# ---- permission gate ------------------------------------------------


def test_execute_promql_requires_app_read(permission_resolver):
    org, app = _scaffold()
    with _ctx(org):
        with pytest.raises(PermissionDenied):
            GoldenSignalsQuery().astrolift_execute_promql(
                _info(),
                app_slug=app.slug,
                query="up",
                start_unix=_START,
                end_unix=_END,
                step_seconds=_STEP,
            )


# ---- validation errors (ok=False) -----------------------------------


def test_execute_promql_empty_query_rejected(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        result = GoldenSignalsQuery().astrolift_execute_promql(
            _info(),
            app_slug=app.slug,
            query="   ",
            start_unix=_START,
            end_unix=_END,
            step_seconds=_STEP,
        )
    assert not result.ok
    assert "query" in result.error


def test_execute_promql_bad_step_rejected(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        result = GoldenSignalsQuery().astrolift_execute_promql(
            _info(),
            app_slug=app.slug,
            query="up",
            start_unix=_START,
            end_unix=_END,
            step_seconds=0,
        )
    assert not result.ok
    assert "step" in result.error


def test_execute_promql_end_before_start_rejected(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        result = GoldenSignalsQuery().astrolift_execute_promql(
            _info(),
            app_slug=app.slug,
            query="up",
            start_unix=_END,
            end_unix=_START,
            step_seconds=_STEP,
        )
    assert not result.ok
    assert "end_unix" in result.error


def test_execute_promql_app_not_found(permission_resolver):
    org, _ = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        result = GoldenSignalsQuery().astrolift_execute_promql(
            _info(),
            app_slug="no-such-app",
            query="up",
            start_unix=_START,
            end_unix=_END,
            step_seconds=_STEP,
        )
    assert not result.ok
    assert "not found" in result.error


def test_execute_promql_no_endpoint_returns_error(permission_resolver):
    org, app = _scaffold(prometheus_endpoint=None)
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        result = GoldenSignalsQuery().astrolift_execute_promql(
            _info(),
            app_slug=app.slug,
            query="up",
            start_unix=_START,
            end_unix=_END,
            step_seconds=_STEP,
        )
    assert not result.ok
    assert "Prometheus" in result.error or "endpoint" in result.error


# ---- prometheus error degrades gracefully ---------------------------


def test_execute_promql_prometheus_error_degrades(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    with (
        _ctx(org),
        patch(
            "astrolift_observability.schema.queries.prometheus_client.query_range",
            side_effect=PrometheusQueryError("invalid expression"),
        ),
    ):
        result = GoldenSignalsQuery().astrolift_execute_promql(
            _info(),
            app_slug=app.slug,
            query="this is invalid {",
            start_unix=_START,
            end_unix=_END,
            step_seconds=_STEP,
        )
    assert not result.ok
    assert "invalid expression" in result.error
    assert result.series == []


# ---- happy path -----------------------------------------------------


def test_execute_promql_returns_series(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    rows = [
        _fake_row({"code": "200"}, [(_START + 60, 1.5), (_START + 120, 2.0)]),
        _fake_row({"code": "500"}, [(_START + 60, 0.1), (_START + 120, 0.2)]),
    ]
    with (
        _ctx(org),
        patch(
            "astrolift_observability.schema.queries.prometheus_client.query_range",
            return_value=rows,
        ),
    ):
        result = GoldenSignalsQuery().astrolift_execute_promql(
            _info(),
            app_slug=app.slug,
            query='sum by (code) (rate(http_requests_total{app="prom-exec-app"}[1m]))',
            start_unix=_START,
            end_unix=_END,
            step_seconds=_STEP,
        )
    assert result.ok, result.error
    assert len(result.series) == 2
    codes = {s.metric_labels["code"] for s in result.series}
    assert codes == {"200", "500"}
    s200 = next(s for s in result.series if s.metric_labels["code"] == "200")
    assert len(s200.values) == 2
    assert s200.values[0].value == pytest.approx(1.5)
    assert s200.values[1].value == pytest.approx(2.0)


def test_execute_promql_empty_labels_on_aggregate(permission_resolver):
    """An unlabelled aggregate (no ``by`` clause) returns a series with an
    empty metric_labels dict rather than failing."""
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    rows = [_fake_row({}, [(_START + 60, 42.0)])]
    with (
        _ctx(org),
        patch(
            "astrolift_observability.schema.queries.prometheus_client.query_range",
            return_value=rows,
        ),
    ):
        result = GoldenSignalsQuery().astrolift_execute_promql(
            _info(),
            app_slug=app.slug,
            query="sum(up)",
            start_unix=_START,
            end_unix=_END,
            step_seconds=_STEP,
        )
    assert result.ok
    assert len(result.series) == 1
    assert result.series[0].metric_labels == {}
    assert result.series[0].values[0].value == pytest.approx(42.0)


def test_execute_promql_no_rows_returns_ok_empty(permission_resolver):
    """Prometheus returning zero rows (metric doesn't exist yet) is ok —
    the series list is empty but ok=True so the FE shows empty charts
    rather than the error state."""
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    with (
        _ctx(org),
        patch(
            "astrolift_observability.schema.queries.prometheus_client.query_range",
            return_value=[],
        ),
    ):
        result = GoldenSignalsQuery().astrolift_execute_promql(
            _info(),
            app_slug=app.slug,
            query="nonexistent_metric",
            start_unix=_START,
            end_unix=_END,
            step_seconds=_STEP,
        )
    assert result.ok
    assert result.series == []
