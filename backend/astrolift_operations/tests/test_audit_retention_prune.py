"""The audit retention sweep reports a true number (``prune_audit_log``).

Real Postgres only - no DB mocks (workspace rule).

The sweep used to import ``AuditLog``, a model that does not exist, and
swallow the ImportError, so the daily schedule reported 0 rows forever
while ``astroliftAuditRetention`` told operators their events were
retained for N days. It now counts against each org's own window.

It counts rather than deletes: ``astrolift_operations/migrations/0003``
binds a ``BEFORE UPDATE OR DELETE`` trigger to the table, so Postgres
refuses the DELETE the schedule is named for. ``test_the_append_only_
trigger_still_refuses_delete`` pins that contradiction so #1594 cannot be
closed by accident - if the trigger is ever lifted, that test fails and
whoever lifted it has to decide what this sweep should do.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from unittest import mock

import pytest
from django.db.utils import InternalError, OperationalError
from django.utils import timezone

from astrolift_identity.models import Organization
from astrolift_operations.models import AuditEvent
from astrolift_workflows.activities.scheduled import _prune_audit_log_sync

pytestmark = pytest.mark.django_db


@contextmanager
def _clock_at(moment: dt.datetime):
    """``occurred_at`` is auto_now_add and the table refuses UPDATE, so a
    row can only be aged by moving the clock at insert time."""
    with mock.patch("django.utils.timezone.now", return_value=moment):
        yield


def _org(*, name: str, retention_days: int) -> Organization:
    return Organization.objects.create(name=name, audit_log_retention_days=retention_days)


def _event(*, org: Organization | None, age_days: int) -> AuditEvent:
    with _clock_at(timezone.now() - dt.timedelta(days=age_days)):
        return AuditEvent.objects.create(
            organization=org,
            actor_kind="system",
            action="test.event",
        )


def test_the_sweep_finds_rows_past_the_window():
    """The regression that mattered: this returned 0 for every row, forever."""
    org = _org(name="acme", retention_days=30)
    _event(org=org, age_days=90)

    assert _prune_audit_log_sync(365) == 1


def test_the_window_is_the_orgs_own_retention_setting():
    short = _org(name="short", retention_days=7)
    long_ = _org(name="long", retention_days=3650)
    _event(org=short, age_days=30)
    _event(org=long_, age_days=30)

    # Same age, same sweep: only the org whose own window has passed counts.
    assert _prune_audit_log_sync(365) == 1


def test_rows_inside_the_window_are_not_counted():
    org = _org(name="acme", retention_days=30)
    _event(org=org, age_days=1)

    assert _prune_audit_log_sync(365) == 0


def test_orgless_rows_fall_back_to_the_activity_default():
    """``AuditEvent.organization`` is SET_NULL, so deleting an org orphans
    its events rather than cascading them away."""
    _event(org=None, age_days=200)

    assert _prune_audit_log_sync(365) == 0
    assert _prune_audit_log_sync(100) == 1


def test_the_append_only_trigger_still_refuses_delete():
    """Why the sweep counts instead of deleting. Guards #1594: lifting the
    trigger without revisiting the sweep should fail here, not in prod."""
    org = _org(name="acme", retention_days=30)
    event = _event(org=org, age_days=90)

    with pytest.raises((InternalError, OperationalError), match="append-only"):
        AuditEvent.objects.filter(pk=event.pk).delete()
