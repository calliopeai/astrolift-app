"""QuotaUsageSnapshot collector + ``astroliftQuotaUsageHistory`` resolver (#1182).

Real Postgres only — no DB mocks (workspace rule). Covers:

* the daily collector appends one snapshot per active quota, is idempotent
  per calendar day, and prunes rows past the retention window;
* the history resolver returns the caller's org-scoped, window-bounded
  history in date order;
* fail-closed cross-org: org A passing org B's quota guid reads back empty
  (indistinguishable from "no snapshots"), never org B's rows;
* the ``BILLING_READ`` permission gate.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from astrolift_billing.models import Quota, QuotaUsageSnapshot
from astrolift_billing.schema.queries import BillingQuery
from astrolift_identity.models import Organization
from astrolift_workflows.activities.scheduled import (
    _QUOTA_USAGE_RETENTION_DAYS,
    _capture_quota_usage_snapshot_sync,
)
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ----------------------------------------------------------------------
# fixtures / helpers
# ----------------------------------------------------------------------


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


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=SimpleNamespace(user=None)))


def _tenant(org):
    return tenant_context(TenantContext(organization_id=org.id))


@pytest.fixture
def org_a():
    return Organization.objects.create(name="Quota Org A", slug="quota-org-a")


@pytest.fixture
def org_b():
    return Organization.objects.create(name="Quota Org B", slug="quota-org-b")


def _quota(org, resource=Quota.Resource.APPS, *, used=42, hard=100, soft=80):
    return Quota.objects.create(
        organization=org,
        scope_kind=Quota.ScopeKind.ORG,
        scope_id=org.id,
        resource=resource,
        hard_limit=hard,
        soft_limit=soft,
        current_usage=used,
    )


def _snapshot(org, quota, day, *, used=1, limit=100):
    return QuotaUsageSnapshot.objects.create(
        organization=org,
        quota=quota,
        captured_at=day,
        used=used,
        limit=limit,
    )


# ----------------------------------------------------------------------
# collector
# ----------------------------------------------------------------------


def test_collector_records_a_snapshot(org_a):
    quota = _quota(org_a, used=42, hard=100)
    created = _capture_quota_usage_snapshot_sync()

    assert created == 1
    row = QuotaUsageSnapshot.objects.get(quota=quota)
    assert row.captured_at == dt.date.today()
    assert float(row.used) == 42.0
    assert float(row.limit) == 100.0
    assert row.organization_id == org_a.id


def test_collector_is_idempotent_same_day(org_a):
    _quota(org_a)
    assert _capture_quota_usage_snapshot_sync() == 1
    # A same-day re-run must not double-write (unique per quota/day).
    assert _capture_quota_usage_snapshot_sync() == 0
    assert QuotaUsageSnapshot.objects.count() == 1


def test_collector_snapshots_every_active_quota(org_a):
    _quota(org_a, Quota.Resource.APPS)
    _quota(org_a, Quota.Resource.CPU)
    # Soft-deleted quota must be skipped.
    dead = _quota(org_a, Quota.Resource.MEMORY)
    dead.deleted_at = dt.datetime.now(dt.UTC)
    dead.save()

    created = _capture_quota_usage_snapshot_sync()
    assert created == 2
    assert QuotaUsageSnapshot.objects.filter(quota=dead).count() == 0


def test_collector_prunes_beyond_retention(org_a):
    quota = _quota(org_a)
    today = dt.date.today()
    stale_day = today - dt.timedelta(days=_QUOTA_USAGE_RETENTION_DAYS + 1)
    kept_day = today - dt.timedelta(days=_QUOTA_USAGE_RETENTION_DAYS - 1)
    _snapshot(org_a, quota, stale_day)
    _snapshot(org_a, quota, kept_day)

    _capture_quota_usage_snapshot_sync()

    days = set(QuotaUsageSnapshot.objects.values_list("captured_at", flat=True))
    assert stale_day not in days
    assert kept_day in days
    assert today in days


# ----------------------------------------------------------------------
# resolver: permission gate
# ----------------------------------------------------------------------


def test_history_denied_without_billing_read(permission_resolver, org_a):
    quota = _quota(org_a)
    with _tenant(org_a):
        with pytest.raises(PermissionDenied):
            BillingQuery().astrolift_quota_usage_history(_info(), quota_id=str(quota.guid))


# ----------------------------------------------------------------------
# resolver: happy path + window
# ----------------------------------------------------------------------


def test_history_returns_points_in_date_order(permission_resolver, org_a):
    quota = _quota(org_a)
    permission_resolver.grant(Permission.BILLING_READ)
    today = dt.date.today()
    # Seed out of order — resolver must return ascending by date.
    _snapshot(org_a, quota, today - dt.timedelta(days=2), used=10, limit=100)
    _snapshot(org_a, quota, today, used=30, limit=100)
    _snapshot(org_a, quota, today - dt.timedelta(days=1), used=20, limit=100)

    with _tenant(org_a):
        out = BillingQuery().astrolift_quota_usage_history(_info(), quota_id=str(quota.guid))

    assert [p.date for p in out] == sorted(p.date for p in out)
    assert [p.used for p in out] == [10.0, 20.0, 30.0]
    assert all(p.limit == 100.0 for p in out)


def test_history_respects_window_days(permission_resolver, org_a):
    quota = _quota(org_a)
    permission_resolver.grant(Permission.BILLING_READ)
    today = dt.date.today()
    _snapshot(org_a, quota, today - dt.timedelta(days=5), used=5)
    _snapshot(org_a, quota, today - dt.timedelta(days=100), used=99)

    with _tenant(org_a):
        default_window = BillingQuery().astrolift_quota_usage_history(_info(), quota_id=str(quota.guid))
        wide_window = BillingQuery().astrolift_quota_usage_history(
            _info(), quota_id=str(quota.guid), window_days=200
        )

    # Default 90d window excludes the 100-day-old point.
    assert [p.used for p in default_window] == [5.0]
    assert sorted(p.used for p in wide_window) == [5.0, 99.0]


def test_history_unknown_quota_returns_empty(permission_resolver, org_a):
    permission_resolver.grant(Permission.BILLING_READ)
    with _tenant(org_a):
        out = BillingQuery().astrolift_quota_usage_history(
            _info(), quota_id="00000000-0000-0000-0000-000000000000"
        )
    assert out == []


# ----------------------------------------------------------------------
# resolver: cross-org isolation (fail-closed)
# ----------------------------------------------------------------------


def test_history_fail_closed_cross_org(permission_resolver, org_a, org_b):
    """Org A must not read org B's quota history by passing B's guid —
    the by-guid fetch carries an ``organization_id`` clause, so a foreign
    guid reads back empty, never B's rows."""
    quota_a = _quota(org_a)
    quota_b = _quota(org_b)
    permission_resolver.grant(Permission.BILLING_READ)
    today = dt.date.today()
    _snapshot(org_a, quota_a, today, used=1)
    # A row that WOULD surface if the resolver weren't org-scoped.
    _snapshot(org_b, quota_b, today, used=9_999)

    with _tenant(org_a):
        own = BillingQuery().astrolift_quota_usage_history(_info(), quota_id=str(quota_a.guid))
        cross = BillingQuery().astrolift_quota_usage_history(_info(), quota_id=str(quota_b.guid))

    assert [p.used for p in own] == [1.0]
    assert cross == []
