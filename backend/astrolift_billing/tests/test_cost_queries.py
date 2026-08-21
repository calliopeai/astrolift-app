"""Resolver tests for the org-level Cost panel (#432).

Covers the four resolver entry points the UI relies on:

* ``astroliftCostSnapshots`` — window-aware listing
* ``astroliftCostTrend`` — daily totals + anomaly flags
* ``astroliftCostForecast`` — month-end projection + MTD delta
* ``astroliftCostByBinding`` — per-resource attribution

Real Postgres only — no DB mocks (workspace rule). The test cases
seed ``CostSnapshot`` rows via the ORM and exercise the resolver
through its ``BillingQuery`` class directly (matches the pattern
in ``astrolift_observability/tests/test_queries.py``).
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from astrolift_billing.models import CostSnapshot
from astrolift_billing.schema.queries import (
    BillingQuery,
    CostWindow,
    ForecastConfidence,
)
from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService, ManagedServiceBinding
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=SimpleNamespace(user=None)))


def _tenant(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _scaffold():
    """Mint a self-contained org + app + binding pair."""
    org = Organization.objects.create(name="Acme Cost", slug="acme-cost")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-cost")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-cost")

    plugin_obj = ProviderPlugin(
        name="K8s",
        slug="k8s-cost",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin_obj])
    plugin = ProviderPlugin.objects.get(slug="k8s-cost")
    cluster = TenantCluster.objects.create(
        organization=org,
        name="prod",
        slug="prod-cost",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://k8s.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="API",
        slug="api-cost",
        k8s_namespace="acme-api",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url="https://api.example.com",
    )
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="main_db",
        variant="rds",
        status=ManagedService.Status.ACTIVE,
    )
    binding = ManagedServiceBinding.objects.create(
        managed_service=svc,
        env_key="DATABASE_URL",
        env_value_ref="secrets://main_db",
    )
    return org, app, svc, binding


def _seed_day(
    *,
    org,
    day: dt.date,
    amount_cents: int,
    by: str = CostSnapshot.CostBy.WORKLOAD,
    app: RegisteredApp | None = None,
    service: ManagedService | None = None,
    source: str = CostSnapshot.Source.PROVIDER_ESTIMATE,
):
    return CostSnapshot.objects.create(
        organization=org,
        registered_app=app,
        managed_service=service,
        taken_at=day,
        by=by,
        amount_cents=amount_cents,
        currency="USD",
        source=source,
    )


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


# ----------------------------------------------------------------------
# permission gate
# ----------------------------------------------------------------------


def test_cost_snapshots_denied_without_billing_read(permission_resolver):
    org, *_ = _scaffold()
    with _tenant(org):
        with pytest.raises(PermissionDenied):
            BillingQuery().astrolift_cost_snapshots(_info())


def test_cost_trend_denied_without_billing_read(permission_resolver):
    org, *_ = _scaffold()
    with _tenant(org):
        with pytest.raises(PermissionDenied):
            BillingQuery().astrolift_cost_trend(_info())


def test_cost_forecast_denied_without_billing_read(permission_resolver):
    org, *_ = _scaffold()
    with _tenant(org):
        with pytest.raises(PermissionDenied):
            BillingQuery().astrolift_cost_forecast(_info())


def test_cost_by_binding_denied_without_billing_read(permission_resolver):
    org, *_ = _scaffold()
    with _tenant(org):
        with pytest.raises(PermissionDenied):
            BillingQuery().astrolift_cost_by_binding(_info())


# ----------------------------------------------------------------------
# window resolution
# ----------------------------------------------------------------------


def test_h24_window_returns_only_today(permission_resolver):
    """The 24h tab maps to a single-day window ending today."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    today = dt.date.today()
    _seed_day(org=org, day=today, amount_cents=100, app=app)
    _seed_day(org=org, day=today - dt.timedelta(days=1), amount_cents=200, app=app)
    with _tenant(org):
        out = BillingQuery().astrolift_cost_snapshots(_info(), window=CostWindow.H24)
    assert len(out) == 1
    assert out[0].amount_cents == 100


def test_d7_window_returns_last_seven_days(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    today = dt.date.today()
    for i in range(10):
        _seed_day(
            org=org,
            day=today - dt.timedelta(days=i),
            amount_cents=10 * (i + 1),
            app=app,
        )
    with _tenant(org):
        out = BillingQuery().astrolift_cost_snapshots(_info(), window=CostWindow.D7)
    # 7 rows: today + 6 prior days
    assert len(out) == 7


def test_d30_window_returns_last_thirty_days(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    today = dt.date.today()
    for i in range(35):
        _seed_day(
            org=org,
            day=today - dt.timedelta(days=i),
            amount_cents=10,
            app=app,
        )
    with _tenant(org):
        out = BillingQuery().astrolift_cost_snapshots(_info(), window=CostWindow.D30)
    assert len(out) == 30


def test_mtd_window_starts_at_first_of_month(permission_resolver):
    """MTD always includes day-1 of the current month, regardless
    of how many days have elapsed."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    today = dt.date.today()
    month_start = today.replace(day=1)
    _seed_day(org=org, day=month_start, amount_cents=500, app=app)
    # one row before the month — must be excluded
    _seed_day(
        org=org,
        day=month_start - dt.timedelta(days=1),
        amount_cents=999,
        app=app,
    )
    with _tenant(org):
        out = BillingQuery().astrolift_cost_snapshots(_info(), window=CostWindow.MTD)
    amounts = sorted(c.amount_cents for c in out)
    assert 500 in amounts
    assert 999 not in amounts


def test_legacy_days_int_still_works(permission_resolver):
    """The pre-#432 ``days`` int parameter is still honored for
    callers that haven't migrated."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    today = dt.date.today()
    _seed_day(org=org, day=today, amount_cents=1, app=app)
    _seed_day(org=org, day=today - dt.timedelta(days=10), amount_cents=2, app=app)
    with _tenant(org):
        out = BillingQuery().astrolift_cost_snapshots(_info(), days=5)
    amounts = sorted(c.amount_cents for c in out)
    assert amounts == [1]


def test_window_takes_precedence_over_days(permission_resolver):
    """When both are passed, ``window`` wins."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    today = dt.date.today()
    _seed_day(org=org, day=today, amount_cents=10, app=app)
    _seed_day(org=org, day=today - dt.timedelta(days=15), amount_cents=20, app=app)
    with _tenant(org):
        out = BillingQuery().astrolift_cost_snapshots(_info(), window=CostWindow.H24, days=30)
    assert len(out) == 1
    assert out[0].amount_cents == 10


# ----------------------------------------------------------------------
# trend + anomaly
# ----------------------------------------------------------------------


def test_trend_has_continuous_xaxis(permission_resolver):
    """Missing days are filled with 0 so the chart can paint a
    continuous line."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    today = dt.date.today()
    # Seed days 0, 2, 5 only — gaps must come back as 0s.
    for offset in (0, 2, 5):
        _seed_day(
            org=org,
            day=today - dt.timedelta(days=offset),
            amount_cents=100,
            app=app,
        )
    with _tenant(org):
        out = BillingQuery().astrolift_cost_trend(_info(), window=CostWindow.D7)
    # 7 contiguous days
    assert len(out) == 7
    dates = [p.date for p in out]
    assert dates == sorted(dates)
    assert all((dates[i + 1] - dates[i]).days == 1 for i in range(len(dates) - 1))
    zero_days = [p.amount_cents for p in out if p.amount_cents == 0]
    assert len(zero_days) == 4


def test_trend_marks_anomalous_spike(permission_resolver):
    """A massive one-day spike vs. a flat baseline must flip
    ``is_anomaly`` on the spike day."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    today = dt.date.today()
    # Flat $1.00/day for 13 days, then a $1000 spike today.
    for offset in range(13, 0, -1):
        _seed_day(
            org=org,
            day=today - dt.timedelta(days=offset),
            amount_cents=100,
            app=app,
        )
    _seed_day(org=org, day=today, amount_cents=100_000, app=app)
    with _tenant(org):
        out = BillingQuery().astrolift_cost_trend(_info(), window=CostWindow.D30)
    anomalies = [p for p in out if p.is_anomaly]
    assert len(anomalies) >= 1
    # The biggest spike is today
    assert anomalies[-1].date == today


def test_trend_no_anomaly_on_flat_series(permission_resolver):
    """No marker when the series is uniform — anomalies are
    relative, not absolute."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    today = dt.date.today()
    for offset in range(7):
        _seed_day(
            org=org,
            day=today - dt.timedelta(days=offset),
            amount_cents=200,
            app=app,
        )
    with _tenant(org):
        out = BillingQuery().astrolift_cost_trend(_info(), window=CostWindow.D7)
    assert not any(p.is_anomaly for p in out)


# ----------------------------------------------------------------------
# forecast
# ----------------------------------------------------------------------


def test_forecast_low_confidence_when_no_data(permission_resolver):
    """Brand-new org with zero snapshots — projection 0, low
    confidence."""
    org, _, _, _ = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    with _tenant(org):
        forecast = BillingQuery().astrolift_cost_forecast(_info())
    assert forecast.projected_monthly_cents == 0
    assert forecast.confidence == ForecastConfidence.LOW
    assert forecast.mtd_cents == 0


def test_forecast_high_confidence_on_stable_series(permission_resolver):
    """14 days of flat $1.00/day spend — confidence = HIGH; the
    projection equals MTD + remaining days * daily rate."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    today = dt.date.today()
    for offset in range(14):
        _seed_day(
            org=org,
            day=today - dt.timedelta(days=offset),
            amount_cents=100,
            app=app,
        )
    with _tenant(org):
        forecast = BillingQuery().astrolift_cost_forecast(_info())
    assert forecast.confidence == ForecastConfidence.HIGH
    assert forecast.projected_monthly_cents > 0


def test_forecast_delta_pct_vs_previous_month(permission_resolver):
    """When previous month had real spend, the delta % is signed
    correctly relative to MTD."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    today = dt.date.today()
    # Previous month: 1000c total on the 1st.
    month_start = today.replace(day=1)
    prev_month_end = month_start - dt.timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)
    _seed_day(org=org, day=prev_month_start, amount_cents=1000, app=app)
    # MTD: 2000c today (so delta = +100%).
    _seed_day(org=org, day=month_start, amount_cents=2000, app=app)
    with _tenant(org):
        forecast = BillingQuery().astrolift_cost_forecast(_info())
    assert forecast.previous_month_cents == 1000
    assert forecast.mtd_cents == 2000
    assert forecast.delta_pct == 100.0


# ----------------------------------------------------------------------
# per-binding attribution
# ----------------------------------------------------------------------


def test_cost_by_binding_groups_by_service(permission_resolver):
    """Rows for the same managed service collapse into one row in
    the attribution table.

    Grouped on ``managed_service_id`` since #1418 — the binding
    column it used to group on is never written, so this test
    passed against an always-empty table."""
    org, app, svc, binding = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    today = dt.date.today()
    _seed_day(
        org=org,
        day=today,
        amount_cents=500,
        app=app,
        service=svc,
        by=CostSnapshot.CostBy.MANAGED_SERVICE,
    )
    _seed_day(
        org=org,
        day=today - dt.timedelta(days=1),
        amount_cents=300,
        app=app,
        service=svc,
        by=CostSnapshot.CostBy.MANAGED_SERVICE,
    )
    with _tenant(org):
        out = BillingQuery().astrolift_cost_by_binding(_info(), window=CostWindow.D7)
    assert out.unattributed_cents == 0
    assert out.total_cents == 800
    assert len(out.attributed_rows) == 1
    row = out.attributed_rows[0]
    assert row.amount_cents == 800
    assert row.managed_service_name == "main_db"
    assert row.managed_service_kind == "postgres"


def test_cost_by_binding_separates_attributed_and_orphan(permission_resolver):
    """Rows the platform did not provision (no
    ``astrolift.io/managed_service_id`` tag on the cloud resource)
    roll into ``unattributed_cents`` rather than fabricating an
    attribution."""
    org, app, svc, _ = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    today = dt.date.today()
    _seed_day(org=org, day=today, amount_cents=400, app=app, service=svc)
    _seed_day(org=org, day=today, amount_cents=600, app=app, service=None)
    with _tenant(org):
        out = BillingQuery().astrolift_cost_by_binding(_info(), window=CostWindow.D7)
    assert out.unattributed_cents == 600
    assert out.total_cents == 1000
    assert len(out.attributed_rows) == 1
    assert out.attributed_rows[0].amount_cents == 400


def test_cost_by_binding_filters_by_app_slug(permission_resolver):
    org, app, svc, binding = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    today = dt.date.today()
    _seed_day(org=org, day=today, amount_cents=100, app=app, service=svc)
    # A row for an empty app slug shouldn't match when we filter.
    _seed_day(org=org, day=today, amount_cents=999, app=None)
    with _tenant(org):
        out = BillingQuery().astrolift_cost_by_binding(
            _info(), window=CostWindow.D7, registered_app_slug=app.slug
        )
    assert out.total_cents == 100


def test_cost_snapshots_are_tenant_scoped(permission_resolver):
    """Cross-org snapshots must not leak into another org's
    view, even when both rows are within the window."""
    org_a, app_a, _, _ = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    other_org = Organization.objects.create(name="Other", slug="other-cost")
    today = dt.date.today()
    _seed_day(org=org_a, day=today, amount_cents=100, app=app_a)
    _seed_day(org=other_org, day=today, amount_cents=9_999_999)
    with _tenant(org_a):
        out = BillingQuery().astrolift_cost_snapshots(_info(), window=CostWindow.D7)
    assert all(c.amount_cents == 100 for c in out)


# ----------------------------------------------------------------------
# month-to-date is a calendar figure, not a rolling-window one (#1240)
# ----------------------------------------------------------------------


@pytest.fixture
def frozen_today(monkeypatch):
    """Pin ``timezone.now()`` so month arithmetic is deterministic.

    The forecast resolver derives every date from "today", so its output
    depends on the calendar date the suite happens to run on. That is how
    #1240 stayed hidden: the defect only surfaces on the 31st.
    """

    def _freeze(day: dt.date):
        frozen = dt.datetime.combine(day, dt.time(12, 0), tzinfo=dt.UTC)
        monkeypatch.setattr("django.utils.timezone.now", lambda: frozen)

    return _freeze


def test_mtd_covers_the_whole_month_on_the_31st(permission_resolver, frozen_today):
    """On the 31st the forecast's 30-day window opens on the 2nd, so an
    MTD summed out of that window drops the 1st entirely. Before the fix
    this reported 0c MTD and a -100% delta on a month that had spent 2000c
    — 7 days a year, on every 31-day month."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    frozen_today(dt.date(2026, 7, 31))

    # Spend on the 1st only: exactly the day the rolling window excludes.
    _seed_day(org=org, day=dt.date(2026, 7, 1), amount_cents=2000, app=app)
    _seed_day(org=org, day=dt.date(2026, 6, 1), amount_cents=1000, app=app)

    with _tenant(org):
        forecast = BillingQuery().astrolift_cost_forecast(_info())

    assert forecast.mtd_cents == 2000
    assert forecast.previous_month_cents == 1000
    assert forecast.delta_pct == 100.0


def test_mtd_matches_the_month_on_a_mid_month_day(permission_resolver, frozen_today):
    """The control: mid-month the window comfortably covers the month, so
    the fix must not change the answer there."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    frozen_today(dt.date(2026, 7, 15))

    _seed_day(org=org, day=dt.date(2026, 7, 1), amount_cents=2000, app=app)
    _seed_day(org=org, day=dt.date(2026, 6, 1), amount_cents=1000, app=app)

    with _tenant(org):
        forecast = BillingQuery().astrolift_cost_forecast(_info())

    assert forecast.mtd_cents == 2000
    assert forecast.delta_pct == 100.0


def test_mtd_excludes_spend_from_before_the_month(permission_resolver, frozen_today):
    """Aggregating over the month rather than the window must not swing
    the other way and pull last month's spend into MTD."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    frozen_today(dt.date(2026, 7, 31))

    # The 30th of June is inside the rolling window but not inside July.
    _seed_day(org=org, day=dt.date(2026, 6, 30), amount_cents=5000, app=app)
    _seed_day(org=org, day=dt.date(2026, 7, 10), amount_cents=700, app=app)

    with _tenant(org):
        forecast = BillingQuery().astrolift_cost_forecast(_info())

    assert forecast.mtd_cents == 700


def test_projection_is_never_below_actual_spend(permission_resolver, frozen_today):
    """A month-end projection below actual month-to-date is not a cautious
    estimate, it is a wrong one — and the same payload carries both, so the
    panel would have read "spent 2000c, projected 0c".

    Reached whenever spend stopped more than 14 days ago: the regression
    window is all zeros, so the resolver bails before adding the actual.
    """
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.BILLING_READ)
    frozen_today(dt.date(2026, 7, 31))

    _seed_day(org=org, day=dt.date(2026, 7, 1), amount_cents=2000, app=app)

    with _tenant(org):
        forecast = BillingQuery().astrolift_cost_forecast(_info())

    assert forecast.mtd_cents == 2000
    assert forecast.projected_monthly_cents >= forecast.mtd_cents
    # No signal to regress on, so the estimate is still flagged untrustworthy.
    assert forecast.confidence == ForecastConfidence.LOW
