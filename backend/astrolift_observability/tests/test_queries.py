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


def test_unknown_app_returns_empty_list(permission_resolver):
    """Soft-empty when the app slug doesn't resolve for the tenant."""
    org, _ = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)
    with _tenant(org):
        result = GoldenSignalsQuery().astrolift_app_golden_signals(_info(), app_slug="does-not-exist")
    assert result == []


def test_app_without_prometheus_endpoint_returns_empty_list(permission_resolver):
    """No ``prometheus_endpoint`` on the cluster → empty state, NOT a
    raise — the FE shows the "metrics not yet flowing" callout."""
    org, app = _scaffold(prometheus_endpoint=None)
    permission_resolver.grant(Permission.APP_READ)
    with _tenant(org):
        result = GoldenSignalsQuery().astrolift_app_golden_signals(_info(), app_slug=app.slug)
    assert result == []


def test_happy_path_returns_six_signals(permission_resolver):
    """Six rows: traffic, errors, p50/p90/p99 latency, cpu saturation."""
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

    assert len(result) == 6
    kinds = [r.name for r in result]
    assert kinds == [
        GoldenSignalKind.TRAFFIC,
        GoldenSignalKind.ERRORS,
        GoldenSignalKind.LATENCY_P50,
        GoldenSignalKind.LATENCY_P90,
        GoldenSignalKind.LATENCY_P99,
        GoldenSignalKind.SATURATION_CPU,
    ]
    for row in result:
        assert row.range_seconds == 60 * 60
        assert len(row.samples) == 2
        assert row.samples[0].value == 1.5
        assert row.samples[1].value == 2.5
        assert row.promql  # non-empty disclosure text


def test_signal_promql_disclosure_includes_app_label(permission_resolver):
    """The dev-mode disclosure text must include the app label so an
    operator can copy-paste the query into Grafana and see it scope
    to their app."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)

    with patch.object(prom_client, "query_range_series", return_value=[]):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_app_golden_signals(_info(), app_slug=app.slug)
    assert all(f'app="{app.slug}"' in row.promql for row in result)


def test_prometheus_error_returns_empty_list(permission_resolver):
    """Any Prometheus failure mid-fan-out → empty state. The FE
    should NEVER see a half-built signal list."""
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)

    def fake_raise(**kwargs):
        raise PrometheusUnavailable("connection refused")

    with patch.object(prom_client, "query_range_series", side_effect=fake_raise):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_app_golden_signals(_info(), app_slug=app.slug)
    assert result == []


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
    assert result, "expected populated list (with empty samples) for clamped range"
    for row in result:
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


def test_status_code_breakdown_returns_none_without_endpoint(permission_resolver):
    """No prometheus_endpoint → null (FE omits the card entirely
    rather than rendering an empty stacked chart)."""
    org, app = _scaffold(prometheus_endpoint=None)
    permission_resolver.grant(Permission.APP_READ)
    with _tenant(org):
        result = GoldenSignalsQuery().astrolift_app_status_code_breakdown(_info(), app_slug=app.slug)
    assert result is None


def test_status_code_breakdown_returns_none_for_unknown_app(permission_resolver):
    org, _ = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)
    with _tenant(org):
        result = GoldenSignalsQuery().astrolift_app_status_code_breakdown(_info(), app_slug="nope")
    assert result is None


def test_status_code_breakdown_returns_none_on_prometheus_error(permission_resolver):
    org, app = _scaffold(prometheus_endpoint="http://prom:9090")
    permission_resolver.grant(Permission.APP_READ)

    def fake_raise(**kwargs):
        raise PrometheusUnavailable("connection refused")

    with patch.object(prom_client, "query_range_series", side_effect=fake_raise):
        with _tenant(org):
            result = GoldenSignalsQuery().astrolift_app_status_code_breakdown(_info(), app_slug=app.slug)
    assert result is None
