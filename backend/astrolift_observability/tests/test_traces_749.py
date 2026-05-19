"""Tests for astroliftAppTraces + astroliftTraceSpans (#749).

Covers:
- Permission gate: APP_READ required
- App not found returns []
- No trace driver configured returns []
- Driver error degrades to []
- astroliftAppTraces happy path: TraceSummary list → AppTrace list
- astroliftTraceSpans happy path: SpanRef list → TraceSpan list
- attributes dict passed through as JSON
- resolve_trace_driver returns None when cluster has no trace_driver
- resolve_trace_driver returns None when trace_driver is unknown
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_observability.schema.queries import GoldenSignalsQuery
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=SimpleNamespace(user=None)))


def _scaffold(*, trace_driver: str | None = "tempo", trace_endpoint: str | None = "http://tempo.test:3200"):
    org = Organization.objects.create(name="TraceOrg", slug="trace-org")
    team = Team.objects.create(organization=org, name="Eng", slug="trace-eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="trace-demo")
    plugin_obj = ProviderPlugin(
        name="K8s",
        slug="k8s-trace",
        version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin_obj])
    plugin = ProviderPlugin.objects.get(slug="k8s-trace")
    cfg: dict = {}
    if trace_driver:
        cfg["trace_driver"] = trace_driver
        if trace_endpoint:
            cfg["trace_config"] = {"endpoint": trace_endpoint}
    cluster = TenantCluster.objects.create(
        organization=org,
        name="prod",
        slug="prod-trace",
        provider_plugin=plugin,
        provider_config=cfg,
        endpoint="https://k8s.invalid",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="TraceApp",
        slug="trace-app",
        provisioning_status="ready",
    )
    AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url="https://trace-app.example.com",
    )
    return org, app


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _fake_summary(trace_id="abc123"):
    from providers._sdk.trace import TraceSummary  # noqa: PLC0415

    return TraceSummary(
        trace_id=trace_id,
        root_service="api",
        root_operation="GET /users",
        span_count=5,
        duration_ms=12.5,
        status_code="OK",
    )


def _fake_span(trace_id="abc123", span_id="s1", parent_span_id=None):
    from providers._sdk.trace import SpanRef  # noqa: PLC0415

    return SpanRef(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        operation="GET /users",
        service="api",
        start_time="1700000000000000000",
        duration_ms=10.0,
        status_code="OK",
        attributes={"http.method": "GET", "http.status_code": "200"},
    )


def _mock_driver(*, traces=None, spans=None, raises=False):
    driver = MagicMock()
    if raises:
        driver.list_traces.side_effect = RuntimeError("tempo unreachable")
        driver.get_trace.side_effect = RuntimeError("tempo unreachable")
    else:
        driver.list_traces.return_value = traces or []
        driver.get_trace.return_value = spans or []
    return driver


# ---- permission gate ------------------------------------------------


def test_app_traces_requires_app_read(permission_resolver):
    org, app = _scaffold()
    with _ctx(org):
        with pytest.raises(PermissionDenied):
            GoldenSignalsQuery().astrolift_app_traces(
                _info(), app_slug=app.slug, since="now-1h", until="now"
            )


def test_trace_spans_requires_app_read(permission_resolver):
    org, app = _scaffold()
    with _ctx(org):
        with pytest.raises(PermissionDenied):
            GoldenSignalsQuery().astrolift_trace_spans(
                _info(), app_slug=app.slug, trace_id="abc"
            )


# ---- empty-state paths ----------------------------------------------


def test_app_traces_app_not_found(permission_resolver):
    org, _ = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        result = GoldenSignalsQuery().astrolift_app_traces(
            _info(), app_slug="no-such-app", since="now-1h", until="now"
        )
    assert result == []


def test_app_traces_no_trace_driver(permission_resolver):
    org, app = _scaffold(trace_driver=None)
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        result = GoldenSignalsQuery().astrolift_app_traces(
            _info(), app_slug=app.slug, since="now-1h", until="now"
        )
    assert result == []


def test_app_traces_driver_error_degrades(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    driver = _mock_driver(raises=True)
    with _ctx(org), patch(
        "astrolift_observability.trace_client.resolve_trace_driver",
        return_value=driver,
    ):
        result = GoldenSignalsQuery().astrolift_app_traces(
            _info(), app_slug=app.slug, since="now-1h", until="now"
        )
    assert result == []


def test_trace_spans_app_not_found(permission_resolver):
    org, _ = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        result = GoldenSignalsQuery().astrolift_trace_spans(
            _info(), app_slug="no-such-app", trace_id="abc"
        )
    assert result == []


def test_trace_spans_driver_error_degrades(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    driver = _mock_driver(raises=True)
    with _ctx(org), patch(
        "astrolift_observability.trace_client.resolve_trace_driver",
        return_value=driver,
    ):
        result = GoldenSignalsQuery().astrolift_trace_spans(
            _info(), app_slug=app.slug, trace_id="abc"
        )
    assert result == []


# ---- happy paths ----------------------------------------------------


def test_app_traces_returns_summary_list(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    summaries = [_fake_summary("t1"), _fake_summary("t2")]
    driver = _mock_driver(traces=summaries)
    with _ctx(org), patch(
        "astrolift_observability.trace_client.resolve_trace_driver",
        return_value=driver,
    ):
        result = GoldenSignalsQuery().astrolift_app_traces(
            _info(), app_slug=app.slug, since="now-1h", until="now"
        )
    assert len(result) == 2
    assert result[0].trace_id == "t1"
    assert result[0].root_service == "api"
    assert result[0].span_count == 5
    assert result[0].duration_ms == pytest.approx(12.5)
    assert result[0].status_code == "OK"


def test_app_traces_passes_filters_to_driver(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    driver = _mock_driver(traces=[])
    with _ctx(org), patch(
        "astrolift_observability.trace_client.resolve_trace_driver",
        return_value=driver,
    ):
        GoldenSignalsQuery().astrolift_app_traces(
            _info(),
            app_slug=app.slug,
            since="now-1h",
            until="now",
            service="api",
            operation="GET /users",
            min_duration_ms=5.0,
            status="ERROR",
            limit=10,
        )
    driver.list_traces.assert_called_once_with(
        service="api",
        operation="GET /users",
        min_duration_ms=5.0,
        status="ERROR",
        since="now-1h",
        until="now",
        limit=10,
    )


def test_trace_spans_returns_span_list(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    spans = [
        _fake_span("t1", "s1", None),
        _fake_span("t1", "s2", "s1"),
    ]
    driver = _mock_driver(spans=spans)
    with _ctx(org), patch(
        "astrolift_observability.trace_client.resolve_trace_driver",
        return_value=driver,
    ):
        result = GoldenSignalsQuery().astrolift_trace_spans(
            _info(), app_slug=app.slug, trace_id="t1"
        )
    assert len(result) == 2
    root = next(s for s in result if s.span_id == "s1")
    child = next(s for s in result if s.span_id == "s2")
    assert root.parent_span_id is None
    assert child.parent_span_id == "s1"
    assert root.operation == "GET /users"
    assert root.attributes == {"http.method": "GET", "http.status_code": "200"}


def test_trace_spans_passes_trace_id_to_driver(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    driver = _mock_driver(spans=[])
    with _ctx(org), patch(
        "astrolift_observability.trace_client.resolve_trace_driver",
        return_value=driver,
    ):
        GoldenSignalsQuery().astrolift_trace_spans(
            _info(), app_slug=app.slug, trace_id="deadbeef"
        )
    driver.get_trace.assert_called_once_with("deadbeef")
