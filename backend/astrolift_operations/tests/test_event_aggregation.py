"""Tests for ``astrolift_events_aggregated`` (#434 scope A).

The aggregator itself is a pure function over an in-memory list, so we
exercise the windowing + bucketing rules without touching the DB. The
resolver end-to-end tests do touch real Postgres (``pytest.mark.django_db``)
to verify permission + filtering wiring on top of live ``Event`` rows;
since ``Event`` is append-only at the DB layer (insert-only triggers),
those tests can only insert rows at their natural wall clock time, so
they assert shape rather than exact gap behavior.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from django.utils import timezone

from astrolift_operations.schema.queries import OperationsQuery, _aggregate_events
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _ctx(org_id: int = 1):
    return tenant_context(TenantContext(organization_id=org_id))


def _row(
    *, when: dt.datetime, event_type: str = "x.fired", resource_id: str = "r-1", resource_kind: str = "k"
):
    """In-memory Event-shaped row. The aggregator ducks-types on the
    fields it reads — full ORM instances aren't required."""
    return SimpleNamespace(
        guid="00000000-0000-0000-0000-000000000000",
        event_type=event_type,
        payload={},
        organization_id=1,
        team_id=None,
        project_id=None,
        registered_app_id=None,
        occurred_at=when,
        resource_kind=resource_kind,
        resource_id=resource_id,
    )


# ---- pure-function aggregator -----------------------------------


def test_aggregator_collapses_identical_within_window():
    """Three identical events within 5 min collapse to one bucket."""
    base = timezone.now().replace(microsecond=0)
    rows = [_row(when=base - dt.timedelta(seconds=offset)) for offset in (0, 60, 120)]
    # newest-first as the resolver fetches it
    rows = sorted(rows, key=lambda r: r.occurred_at, reverse=True)

    buckets = _aggregate_events(rows, window_seconds=300, limit=10)

    assert len(buckets) == 1
    assert buckets[0].count == 3
    assert buckets[0].event_type == "x.fired"
    assert buckets[0].first_at == rows[-1].occurred_at
    assert buckets[0].last_at == rows[0].occurred_at


def test_aggregator_splits_across_window_boundary():
    """A gap larger than the window opens a new bucket even when
    the key matches."""
    base = timezone.now().replace(microsecond=0)
    rows = [_row(when=base - dt.timedelta(seconds=offset)) for offset in (0, 60, 700)]
    rows = sorted(rows, key=lambda r: r.occurred_at, reverse=True)

    buckets = _aggregate_events(rows, window_seconds=300, limit=10)

    assert len(buckets) == 2
    assert buckets[0].count == 2
    assert buckets[1].count == 1


def test_aggregator_separates_by_resource_id():
    """Different resource_id values never roll together even when
    event_type matches."""
    base = timezone.now().replace(microsecond=0)
    rows = [
        _row(when=base, resource_id="app-1"),
        _row(when=base - dt.timedelta(seconds=30), resource_id="app-2"),
    ]
    rows = sorted(rows, key=lambda r: r.occurred_at, reverse=True)

    buckets = _aggregate_events(rows, window_seconds=300, limit=10)

    assert len(buckets) == 2
    counts = {b.resource_id: b.count for b in buckets}
    assert counts == {"app-1": 1, "app-2": 1}


def test_aggregator_caps_at_limit():
    """``limit`` caps the bucket count even when more keys exist."""
    base = timezone.now().replace(microsecond=0)
    rows = [
        _row(when=base - dt.timedelta(seconds=i), event_type=f"e.{i}", resource_id=f"r-{i}") for i in range(5)
    ]
    rows = sorted(rows, key=lambda r: r.occurred_at, reverse=True)

    buckets = _aggregate_events(rows, window_seconds=300, limit=2)
    assert len(buckets) == 2


def test_aggregator_empty_returns_empty():
    assert _aggregate_events([], window_seconds=300, limit=10) == []


# ---- resolver end-to-end ----------------------------------------


@pytest.mark.django_db
def test_resolver_requires_permission():
    """Without a grant, the @require_permission decorator denies."""
    from astrolift_identity.models import Organization
    from core.permissions import PermissionDenied

    org = Organization.objects.create(name="Acme", slug="acme-perm-test")
    with _ctx(org.id), pytest.raises(PermissionDenied):
        OperationsQuery().astrolift_events_aggregated(_info(), limit=10)


@pytest.mark.django_db
def test_resolver_returns_aggregated_buckets(permission_resolver):
    """Live DB: real Event rows roll up by ``(event_type, resource_*)``.

    Rows are inserted back-to-back so they all fall inside any
    reasonable aggregate window; we assert that identical keys
    collapse and distinct keys stay separate. The DB's append-only
    trigger refuses UPDATEs on Event, so we can't backdate
    ``occurred_at`` — gap-windowing is exercised by the pure-function
    tests above.
    """
    from astrolift_identity.models import Organization
    from astrolift_operations.models import Event

    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    org = Organization.objects.create(name="Acme", slug="acme-agg-test")

    for _ in range(3):
        Event.objects.create(
            organization=org,
            event_type="pod.restart",
            resource_kind="workload",
            resource_id="web",
        )
    Event.objects.create(
        organization=org,
        event_type="deploy.completed",
        resource_kind="deployment",
        resource_id="deploy-xyz",
    )

    with _ctx(org.id):
        buckets = OperationsQuery().astrolift_events_aggregated(
            _info(),
            limit=10,
            aggregate_window_seconds=300,
        )
    by_type = {b.event_type: b for b in buckets}
    assert by_type["pod.restart"].count == 3
    assert by_type["deploy.completed"].count == 1


@pytest.mark.django_db
def test_resolver_filters_by_event_type(permission_resolver):
    from astrolift_identity.models import Organization
    from astrolift_operations.models import Event

    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    org = Organization.objects.create(name="Acme", slug="acme-filter-test")
    Event.objects.create(organization=org, event_type="pod.restart")
    Event.objects.create(organization=org, event_type="deploy.completed")

    with _ctx(org.id):
        buckets = OperationsQuery().astrolift_events_aggregated(
            _info(),
            limit=10,
            event_type="pod.restart",
        )
    assert len(buckets) == 1
    assert buckets[0].event_type == "pod.restart"
