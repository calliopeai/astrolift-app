"""Resolver tests for the App > Observability golden-signals surface
(#380).

We mock ``prom_client.query_range_series`` (the network boundary) so
the tests are deterministic without standing up a Prometheus. The
underlying urllib transport is covered by
``astrolift_operations/tests/test_prometheus_client.py``.

Coverage:

* permission denied → resolver raises (the ``@require_permission``
  decorator); we use the ``permission_resolver`` fixture's deny path
* unknown app slug → ``[]`` / ``None``
* cluster has no ``prometheus_endpoint`` → ``[]`` / ``None`` (empty
  state — "metrics not yet flowing")
* happy path → six golden-signal rows, samples shaped correctly
* status-code breakdown groups by class + picks top codes
* Prometheus error degrades to empty state
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_observability import prom_client
from astrolift_observability.schema.queries import GoldenSignalsQuery
from astrolift_observability.schema.types import GoldenSignalKind
from astrolift_operations.prometheus_client import PrometheusUnavailable
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission, PermissionDenied
from core.schema.enums import ObservabilityPanelReason
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=SimpleNamespace(user=None)))


def _scaffold(*, prometheus_endpoint: str | None = None) -> tuple[Organization, RegisteredApp]:
    """Mint a self-contained org + cluster + app + env. Caller picks
    whether the cluster has a Prometheus endpoint."""
    org = Organization.objects.create(name="Acme Obs", slug="acme-obs")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-obs")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-obs")

    # ``ProviderPlugin.save`` increments ``version`` on every write —
    # mirror the operations test suite and use ``bulk_create`` to skip
    # the custom save path.
    plugin_obj = ProviderPlugin(
        name="K8s",
        slug="k8s-obs",
        version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin_obj])
    plugin = ProviderPlugin.objects.get(slug="k8s-obs")

    cfg: dict = {}
    if prometheus_endpoint:
        cfg["prometheus_endpoint"] = prometheus_endpoint
    cluster = TenantCluster.objects.create(
        organization=org,
        name="prod",
        slug="prod-obs",
        provider_plugin=plugin,
        provider_config=cfg,
        endpoint="https://k8s.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello Obs",
        slug="hello-obs",
        k8s_namespace="acme-hello",
        provisioning_status="ready",
    )
    AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url="https://hello-obs.example.com",
    )
    return org, app


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    """Mirror the operations test suite — keep the profile indexer
    quiet so an OpenSearch outage doesn't fail the obs tests."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


def _tenant(org):
    return tenant_context(TenantContext(organization_id=org.id))


# ----------------------------------------------------------------------
# astroliftAppGoldenSignals
# ----------------------------------------------------------------------


def test_unknown_app_returns_not_configured(permission_resolver):
    """Unknown app for the tenant → NOT_CONFIGURED envelope, empty
    signals (#1111)."""
    org, _ = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)
    with _tenant(org):
        result = GoldenSignalsQuery().astrolift_app_golden_signals(_info(), app_slug="does-not-exist")
    assert result.reason == ObservabilityPanelReason.NOT_CONFIGURED
    assert result.signals == []


def test_app_without_prometheus_endpoint_reports_not_configured(permission_resolver):
    """No ``prometheus_endpoint`` on the cluster → NOT_CONFIGURED, NOT a
    raise — the FE shows "Prometheus endpoint isn't wired"."""
    org, app = _scaffold(prometheus_endpoint=None)
    permission_resolver.grant(Permission.APP_READ)
    with _tenant(org):
        result = GoldenSignalsQuery().astrolift_app_golden_signals(_info(), app_slug=app.slug)
    assert result.reason == ObservabilityPanelReason.NOT_CONFIGURED
    assert result.signals == []


def test_happy_path_returns_eight_signals(permission_resolver):
    """Eight rows: traffic, errors, p50/p90/p95/p99 latency,
    cpu+memory saturation.

    #640 added p95; #642 added memory saturation. Order is fixed so
    the FE can render the latency card without re-sorting.
    """
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)

    # Two-sample series, same shape across every signal — keeps the
    # assertion focused on resolver shaping, not query semantics.
    def fake_query_range_series(
        *,
        endpoint,
        promql,
        start_unix,
        end_unix,
        step_seconds,
        label_key=None,
    ):
        # One series with two samples.
        return [("", [(float(start_unix), 1.5), (float(end_unix), 2.5)])]

    with patch.object(prom_client, "query_range_series", side_effect=fake_query_range_series):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_app_golden_signals(
                _info(),
                app_slug=app.slug,
                range_seconds=60 * 60,
            )

    assert result.reason == ObservabilityPanelReason.OK
    assert len(result.signals) == 8
    kinds = [r.name for r in result.signals]
    assert kinds == [
        GoldenSignalKind.TRAFFIC,
        GoldenSignalKind.ERRORS,
        GoldenSignalKind.LATENCY_P50,
        GoldenSignalKind.LATENCY_P90,
        GoldenSignalKind.LATENCY_P95,
        GoldenSignalKind.LATENCY_P99,
        GoldenSignalKind.SATURATION_CPU,
        GoldenSignalKind.SATURATION_MEMORY,
    ]
    for row in result.signals:
        assert row.range_seconds == 60 * 60
        assert len(row.samples) == 2
        assert row.samples[0].value == 1.5
        assert row.samples[1].value == 2.5
        assert row.promql  # non-empty disclosure text


def test_p95_promql_carries_quantile_value(permission_resolver):
    """#640 — the p95 row must carry the literal ``0.95`` quantile in
    its PromQL so operators pasting into Grafana get the SLO-canonical
    series, not an off-by-one approximation."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)

    with patch.object(prom_client, "query_range_series", return_value=[]):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_app_golden_signals(
                _info(),
                app_slug=app.slug,
            )

    p95 = next(r for r in result.signals if r.name == GoldenSignalKind.LATENCY_P95)
    assert "histogram_quantile(0.95" in p95.promql


def test_memory_saturation_uses_working_set_bytes(permission_resolver):
    """#642 — the memory saturation row must use the working-set bytes
    gauge (cAdvisor's OOM-accounting metric) over the memory limit,
    not a guess like RSS or cache."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)

    with patch.object(prom_client, "query_range_series", return_value=[]):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_app_golden_signals(
                _info(),
                app_slug=app.slug,
            )

    mem = next(r for r in result.signals if r.name == GoldenSignalKind.SATURATION_MEMORY)
    assert "container_memory_working_set_bytes" in mem.promql
    assert 'resource="memory"' in mem.promql


def test_signal_promql_disclosure_includes_app_label(permission_resolver):
    """The dev-mode disclosure text must scope every signal to the app so
    an operator can copy-paste the query into Grafana. App-instrumentation
    signals carry the ``app="<slug>"`` matcher; the saturation pair reads
    cAdvisor / kube-state-metrics series that only carry namespace/pod/
    container labels, so those scope by ``namespace="<ns>"`` instead."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)

    with patch.object(prom_client, "query_range_series", return_value=[]):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_app_golden_signals(_info(), app_slug=app.slug)
    namespace_scoped = {GoldenSignalKind.SATURATION_CPU, GoldenSignalKind.SATURATION_MEMORY}
    for row in result.signals:
        if row.name in namespace_scoped:
            assert f'namespace="{app.k8s_namespace}"' in row.promql, row.promql
            assert f'app="{app.slug}"' not in row.promql, row.promql
        else:
            assert f'app="{app.slug}"' in row.promql, row.promql


def test_prometheus_error_reports_error(permission_resolver):
    """Any Prometheus failure mid-fan-out → ERROR reason, empty signals.
    The FE should NEVER see a half-built signal list, and the reason is
    distinct from "no data" so the FE can offer a retry."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)

    def fake_raise(**kwargs):
        raise PrometheusUnavailable("connection refused")

    with patch.object(prom_client, "query_range_series", side_effect=fake_raise):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_app_golden_signals(_info(), app_slug=app.slug)
    assert result.reason == ObservabilityPanelReason.ERROR
    assert result.signals == []


def test_permission_denied_raises(permission_resolver):
    """Without ``APP_READ`` the decorator raises — the resolver itself
    never sees the call."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.deny(Permission.APP_READ)
    with _tenant(org), pytest.raises(PermissionDenied):
        GoldenSignalsQuery().astrolift_app_golden_signals(_info(), app_slug=app.slug)


def test_range_seconds_clamped_to_ceiling(permission_resolver):
    """A caller can't ask Prometheus for a year of data. The resolver
    clamps to 31 days."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)

    with patch.object(prom_client, "query_range_series", return_value=[]):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_app_golden_signals(
                _info(),
                app_slug=app.slug,
                range_seconds=365 * 86400,
            )
    # Empty samples across the board → NO_DATA_YET, but every signal
    # row is still present (with the clamped range) so the FE can show
    # the card skeleton.
    assert result.reason == ObservabilityPanelReason.NO_DATA_YET
    assert result.signals, "expected populated list (with empty samples) for clamped range"
    for row in result.signals:
        assert row.range_seconds == 31 * 86400


# ----------------------------------------------------------------------
# astroliftAppStatusCodeBreakdown
# ----------------------------------------------------------------------


def test_status_code_breakdown_groups_into_class_buckets(permission_resolver):
    """Per-code rows aggregate into 2xx/3xx/4xx/5xx classes; the
    top-codes list ranks codes by total volume."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)

    # Fake matrix: 200 dominates 2xx, 500 + 502 split 5xx, one weird
    # value goes to "other".
    fake_rows = [
        ("200", [(1700000000.0, 10.0), (1700000060.0, 20.0)]),
        ("201", [(1700000000.0, 1.0), (1700000060.0, 1.0)]),
        ("500", [(1700000000.0, 0.5), (1700000060.0, 0.5)]),
        ("502", [(1700000000.0, 0.2), (1700000060.0, 0.1)]),
        ("garbage", [(1700000000.0, 0.0), (1700000060.0, 0.0)]),
    ]

    def fake_query_range_series(**kwargs):
        return fake_rows

    with patch.object(prom_client, "query_range_series", side_effect=fake_query_range_series):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_app_status_code_breakdown(
                _info(),
                app_slug=app.slug,
                range_seconds=60 * 60,
            )
    assert result is not None
    assert result.reason == ObservabilityPanelReason.OK
    classes = {s.code_class for s in result.series}
    assert classes == {"2xx", "5xx", "other"}

    twoxx = next(s for s in result.series if s.code_class == "2xx")
    # 200 had total 30 vs 201 total 2 — 200 ranks first in top_codes.
    assert twoxx.top_codes[0] == "200"
    # Aggregated samples: at ts=1700000000, sum of 10 + 1 = 11.
    assert twoxx.samples[0].value == pytest.approx(11.0)
    assert twoxx.samples[1].value == pytest.approx(21.0)

    fivexx = next(s for s in result.series if s.code_class == "5xx")
    # 500 (1.0 total) ranks above 502 (0.3 total).
    assert fivexx.top_codes == ["500", "502"]


def test_status_code_breakdown_not_configured_without_endpoint(permission_resolver):
    """No prometheus_endpoint → an object carrying NOT_CONFIGURED and
    empty series (#1111), so the FE renders "Prometheus endpoint isn't
    wired" instead of silently omitting the card. ``null`` is now
    reserved for "no such app"."""
    org, app = _scaffold(prometheus_endpoint=None)
    permission_resolver.grant(Permission.APP_READ)
    with _tenant(org):
        result = GoldenSignalsQuery().astrolift_app_status_code_breakdown(_info(), app_slug=app.slug)
    assert result is not None
    assert result.reason == ObservabilityPanelReason.NOT_CONFIGURED
    assert result.series == []


def test_status_code_breakdown_returns_none_for_unknown_app(permission_resolver):
    org, _ = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)
    with _tenant(org):
        result = GoldenSignalsQuery().astrolift_app_status_code_breakdown(_info(), app_slug="nope")
    assert result is None


def test_status_code_breakdown_reports_error_on_prometheus_error(permission_resolver):
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)

    def fake_raise(**kwargs):
        raise PrometheusUnavailable("connection refused")

    with patch.object(prom_client, "query_range_series", side_effect=fake_raise):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_app_status_code_breakdown(_info(), app_slug=app.slug)
    assert result is not None
    assert result.reason == ObservabilityPanelReason.ERROR
    assert result.series == []


# ----------------------------------------------------------------------
# workload_slug scoping (#422)
# ----------------------------------------------------------------------


def test_golden_signals_workload_slug_threads_into_promql(permission_resolver):
    """The ``workload_slug`` resolver arg lands as a ``workload="<slug>"``
    matcher in every app-instrumentation signal so Prometheus narrows the
    metric stream to one workload. The saturation pair reads cAdvisor /
    kube-state-metrics series that carry no ``workload`` label, so those
    stay namespace-scoped (the workload matcher is intentionally dropped
    rather than producing a query that matches nothing)."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)

    with patch.object(prom_client, "query_range_series", return_value=[]):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_app_golden_signals(
                _info(),
                app_slug=app.slug,
                environment_name="prod",
                workload_slug="api",
                range_seconds=60 * 60,
            )

    assert result.signals, "expected one row per signal kind even when samples are empty"
    namespace_scoped = {GoldenSignalKind.SATURATION_CPU, GoldenSignalKind.SATURATION_MEMORY}
    for row in result.signals:
        if row.name in namespace_scoped:
            assert 'workload="api"' not in row.promql, row.promql
            assert f'namespace="{app.k8s_namespace}"' in row.promql, row.promql
        else:
            assert 'workload="api"' in row.promql, row.promql


def test_golden_signals_without_workload_slug_omits_label(permission_resolver):
    """Omitting ``workload_slug`` preserves pre-#422 behavior — no
    ``workload=`` label in the PromQL, so the query rolls every
    workload up."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)

    with patch.object(prom_client, "query_range_series", return_value=[]):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_app_golden_signals(
                _info(),
                app_slug=app.slug,
                environment_name="prod",
                range_seconds=60 * 60,
            )

    assert result.signals
    for row in result.signals:
        assert "workload=" not in row.promql, row.promql


def test_status_code_breakdown_workload_slug_threads_into_promql(permission_resolver):
    """The status-code breakdown query mirrors the golden-signals
    scoping — ``workload_slug`` appears verbatim in the PromQL."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)

    with patch.object(prom_client, "query_range_series", return_value=[]):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_app_status_code_breakdown(
                _info(),
                app_slug=app.slug,
                environment_name="prod",
                workload_slug="worker",
                range_seconds=60 * 60,
            )

    assert result is not None
    assert 'workload="worker"' in result.promql


# ----------------------------------------------------------------------
# astroliftWorkloadResourceUsage (#430)
# ----------------------------------------------------------------------


def test_workload_resource_usage_unknown_app_returns_null(permission_resolver):
    """Soft-null when the app slug doesn't resolve for the tenant."""
    org, _ = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)
    with _tenant(org):
        result = GoldenSignalsQuery().astrolift_workload_resource_usage(
            _info(),
            app_slug="nope",
            workload_slug="api",
        )
    assert result is None


def test_workload_resource_usage_no_prometheus_endpoint_returns_null(permission_resolver):
    """No prom endpoint → null, FE shows the metrics-not-flowing card."""
    org, app = _scaffold(prometheus_endpoint=None)
    permission_resolver.grant(Permission.APP_READ)
    with _tenant(org):
        result = GoldenSignalsQuery().astrolift_workload_resource_usage(
            _info(),
            app_slug=app.slug,
            workload_slug="api",
        )
    assert result is None


def test_workload_resource_usage_happy_path(permission_resolver):
    """Six instant queries (usage+request+limit × CPU+memory) → gauge
    pair with percent fields filled."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)

    # Return values keyed by the substring of the PromQL — keeps the
    # stub readable without re-parsing.
    def fake_query_instant(*, endpoint, query, timeout=5.0):
        if "container_cpu_usage_seconds_total" in query:
            return 0.5  # 0.5 cores used
        if "container_memory_working_set_bytes" in query:
            return 256.0 * 1024 * 1024  # 256 MiB used
        if "kube_pod_container_resource_requests" in query and 'resource="cpu"' in query:
            return 1.0
        if "kube_pod_container_resource_limits" in query and 'resource="cpu"' in query:
            return 2.0
        if "kube_pod_container_resource_requests" in query and 'resource="memory"' in query:
            return 512.0 * 1024 * 1024
        if "kube_pod_container_resource_limits" in query and 'resource="memory"' in query:
            return 1024.0 * 1024 * 1024
        raise AssertionError(f"unexpected query {query!r}")

    from astrolift_operations import prometheus_client as ops_client

    with patch.object(ops_client, "query_instant", side_effect=fake_query_instant):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_workload_resource_usage(
                _info(),
                app_slug=app.slug,
                workload_slug="api",
                environment_name="prod",
            )

    assert result is not None
    assert result.cpu.current == pytest.approx(0.5)
    assert result.cpu.request == pytest.approx(1.0)
    assert result.cpu.limit == pytest.approx(2.0)
    assert result.cpu.percent_of_request == pytest.approx(50.0)
    assert result.cpu.percent_of_limit == pytest.approx(25.0)
    assert result.cpu.unit == "cores"
    assert result.memory.current == pytest.approx(256.0 * 1024 * 1024)
    assert result.memory.percent_of_request == pytest.approx(50.0)
    assert result.memory.percent_of_limit == pytest.approx(25.0)
    assert result.memory.unit == "bytes"


def test_workload_resource_usage_all_errors_returns_null(permission_resolver):
    """Every prom call failing on both resources → null (the
    Prometheus-is-dark signal)."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)

    def fake_raise(**kwargs):
        raise PrometheusUnavailable("connection refused")

    from astrolift_operations import prometheus_client as ops_client

    with patch.object(ops_client, "query_instant", side_effect=fake_raise):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_workload_resource_usage(
                _info(),
                app_slug=app.slug,
                workload_slug="api",
            )
    assert result is None


def test_workload_resource_usage_partial_errors_zero_denominator(permission_resolver):
    """A single failed call → field is 0.0, percent collapses safely."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)

    def fake_query_instant(*, endpoint, query, timeout=5.0):
        if "container_cpu_usage_seconds_total" in query:
            return 0.5
        if "container_memory_working_set_bytes" in query:
            return 0.0
        # All request/limit calls succeed with 0 → percent must be 0,
        # not a ZeroDivisionError.
        return 0.0

    from astrolift_operations import prometheus_client as ops_client

    with patch.object(ops_client, "query_instant", side_effect=fake_query_instant):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_workload_resource_usage(
                _info(),
                app_slug=app.slug,
                workload_slug="api",
            )
    assert result is not None
    assert result.cpu.percent_of_request == 0.0
    assert result.cpu.percent_of_limit == 0.0


def test_workload_resource_usage_permission_denied_raises(permission_resolver):
    """Without ``APP_READ`` the decorator raises before the resolver
    runs — same shape as every other observability query."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    # No grant.
    with _tenant(org), pytest.raises(PermissionDenied):
        GoldenSignalsQuery().astrolift_workload_resource_usage(
            _info(),
            app_slug=app.slug,
            workload_slug="api",
        )


# ----------------------------------------------------------------------
# astroliftAppManagedServiceMetrics (#645 + #646)
# ----------------------------------------------------------------------


def _managed_service(*, app, kind, name="primary"):
    """Mint a ManagedService row for the test scaffolded app.

    The test app has one env (``prod``) hanging off it from the
    scaffold; reuse that to satisfy the foreign-key constraint.
    """
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_services.models.managed_service import ManagedService as MS

    env = AppEnvironment.objects.get(registered_app=app, name="prod")
    return MS.objects.create(
        registered_app=app,
        app_environment=env,
        kind=kind,
        name=name,
        variant="",
        config={},
        status=MS.Status.ACTIVE,
    )


def test_managed_service_metrics_postgres_returns_five_series(permission_resolver):
    """Postgres kind → five metric series with the canonical names so
    the FE renders a fixed card grid regardless of how many series
    actually carry samples."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)
    svc = _managed_service(app=app, kind="postgres")

    def fake_query(**kwargs):
        return [("", [(1700000000.0, 3.0), (1700000060.0, 4.0)])]

    with patch.object(prom_client, "query_range_series", side_effect=fake_query):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_app_managed_service_metrics(
                _info(),
                managed_service_id=str(svc.guid),
                range_seconds=60 * 60,
            )

    assert result is not None
    assert result.kind == "postgres"
    names = [s.name for s in result.series]
    assert names == ["connections", "cpu", "iops", "slow_queries", "replica_lag"]
    for s in result.series:
        assert s.source == "prometheus"
        assert len(s.samples) == 2


def test_managed_service_metrics_object_store_returns_five_series(permission_resolver):
    """Object-store kind → five metric series with the canonical S3
    panel names."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)
    svc = _managed_service(app=app, kind="object_store")

    def fake_query(**kwargs):
        return [("", [(1700000000.0, 1.0)])]

    with patch.object(prom_client, "query_range_series", side_effect=fake_query):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_app_managed_service_metrics(
                _info(),
                managed_service_id=str(svc.guid),
            )

    assert result is not None
    assert result.kind == "object_store"
    names = [s.name for s in result.series]
    assert names == [
        "bucket_size",
        "request_count",
        "errors_4xx",
        "errors_5xx",
        "egress_bytes",
    ]


def test_managed_service_metrics_unsupported_kind_returns_none(permission_resolver):
    """Anything other than postgres / object_store returns ``None`` so
    the FE skips the panel entirely (rather than rendering an empty
    card). Follow-up tickets add coverage for other kinds."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)
    svc = _managed_service(app=app, kind="redis")

    with _tenant(org):
        result = GoldenSignalsQuery().astrolift_app_managed_service_metrics(
            _info(),
            managed_service_id=str(svc.guid),
        )

    assert result is None


def test_managed_service_metrics_missing_service_returns_none(permission_resolver):
    """Unknown managed-service guid → ``None``."""
    org, _ = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)

    with _tenant(org):
        result = GoldenSignalsQuery().astrolift_app_managed_service_metrics(
            _info(),
            managed_service_id="00000000-0000-0000-0000-000000000000",
        )

    assert result is None


def test_managed_service_metrics_no_prometheus_returns_empty_envelope(permission_resolver):
    """No ``prometheus_endpoint`` on the cluster → empty series list
    (not None) so the FE renders the panel header + the "metrics not
    yet flowing" callout inside it. The kind/name still come through
    so the operator sees what was supposed to render."""
    org, app = _scaffold(prometheus_endpoint=None)
    permission_resolver.grant(Permission.APP_READ)
    svc = _managed_service(app=app, kind="postgres")

    with _tenant(org):
        result = GoldenSignalsQuery().astrolift_app_managed_service_metrics(
            _info(),
            managed_service_id=str(svc.guid),
        )

    assert result is not None
    assert result.series == []
    assert result.kind == "postgres"


def test_managed_service_metrics_permission_denied_raises(permission_resolver):
    """Same deny path as every observability resolver."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    svc = _managed_service(app=app, kind="postgres")
    # No grant.
    with _tenant(org), pytest.raises(PermissionDenied):
        GoldenSignalsQuery().astrolift_app_managed_service_metrics(
            _info(),
            managed_service_id=str(svc.guid),
        )


# ----------------------------------------------------------------------
# astroliftPodResourceUsage (#713)
# ----------------------------------------------------------------------


def test_pod_resource_usage_returns_paired_cpu_memory_samples(permission_resolver):
    """Per-pod usage carries CPU + memory in the same point so the FE
    can render the twin sparkline in one request."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)

    samples_by_metric = {}

    def fake_query(**kwargs):
        # Two queries fan out — CPU first, memory second.
        promql = kwargs["promql"]
        if "cpu_usage_seconds" in promql:
            samples_by_metric["cpu"] = True
            return [("", [(1700000000.0, 0.25), (1700000060.0, 0.35)])]
        if "memory_working_set" in promql:
            samples_by_metric["mem"] = True
            return [("", [(1700000000.0, 1024.0), (1700000060.0, 2048.0)])]
        return []

    with patch.object(prom_client, "query_range_series", side_effect=fake_query):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_pod_resource_usage(
                _info(),
                app_slug=app.slug,
                pod_name="hello-obs-api-0",
                range_seconds=60 * 60,
            )

    assert result is not None
    assert result.reason == ObservabilityPanelReason.OK
    assert samples_by_metric == {"cpu": True, "mem": True}
    assert len(result.samples) == 2
    assert result.samples[0].cpu_cores == 0.25
    assert result.samples[0].memory_bytes == 1024.0
    assert result.samples[1].cpu_cores == 0.35
    assert result.samples[1].memory_bytes == 2048.0


def test_pod_resource_usage_no_endpoint_reports_not_configured(permission_resolver):
    """No Prometheus endpoint on the cluster → NOT_CONFIGURED envelope
    with empty samples (#1111), so the expander says why rather than
    showing a stale 0%."""
    org, app = _scaffold(prometheus_endpoint=None)
    permission_resolver.grant(Permission.APP_READ)

    with _tenant(org):
        result = GoldenSignalsQuery().astrolift_pod_resource_usage(
            _info(),
            app_slug=app.slug,
            pod_name="hello-obs-api-0",
        )

    assert result is not None
    assert result.reason == ObservabilityPanelReason.NOT_CONFIGURED
    assert result.samples == []


def test_pod_resource_usage_unknown_app_returns_none(permission_resolver):
    org, _ = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)
    with _tenant(org):
        result = GoldenSignalsQuery().astrolift_pod_resource_usage(
            _info(),
            app_slug="does-not-exist",
            pod_name="x",
        )
    assert result is None


def test_pod_resource_usage_permission_denied_raises(permission_resolver):
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    # No grant.
    with _tenant(org), pytest.raises(PermissionDenied):
        GoldenSignalsQuery().astrolift_pod_resource_usage(
            _info(),
            app_slug=app.slug,
            pod_name="x",
        )
