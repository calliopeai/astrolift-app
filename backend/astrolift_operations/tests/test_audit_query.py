"""Tests for the audit query surface (#433 scopes A, B, D)."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from django.utils import timezone

from astrolift_identity.models import Organization
from astrolift_operations.audit_redaction import REDACTED_SENTINEL
from astrolift_operations.models import AuditEvent
from astrolift_operations.schema.queries import OperationsQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None))


def _mkorg() -> Organization:
    return Organization.objects.create(name="QueryOrg", slug="query-org")


def _mkaudit(
    org: Organization,
    action: str = "team.create",
    *,
    occurred_at: dt.datetime | None = None,
    **extra,
) -> AuditEvent:
    """AuditEvent is append-only at both the app and DB layer: the DB
    trigger refuses UPDATE so we can't rewrite ``occurred_at`` after
    insert. ``bulk_create`` bypasses ``auto_now_add`` so the caller can
    place the row at a specific point in time when needed."""
    if occurred_at is not None:
        return AuditEvent.objects.bulk_create(
            [
                AuditEvent(
                    organization=org,
                    occurred_at=occurred_at,
                    actor_kind="user",
                    actor_id="42",
                    actor_display="alice",
                    action=action,
                    decision=AuditEvent.Decision.ALLOW,
                    target_kind="Team",
                    target_id="t-1",
                    target_slug="alpha",
                    data=extra or {"reason": "ok"},
                )
            ]
        )[0]
    return AuditEvent.objects.create(
        organization=org,
        actor_kind="user",
        actor_id="42",
        actor_display="alice",
        action=action,
        decision=AuditEvent.Decision.ALLOW,
        target_kind="Team",
        target_id="t-1",
        target_slug="alpha",
        data=extra or {"reason": "ok"},
    )


def test_audit_data_field_is_redacted_on_read(permission_resolver):
    org = _mkorg()
    _mkaudit(org, secret="shhh", reason="ok")
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        q = OperationsQuery()
        rows = q.astrolift_audit_events(_info(), limit=10)
        assert len(rows) == 1
        assert rows[0].data["secret"] == REDACTED_SENTINEL
        assert rows[0].data["reason"] == "ok"


def test_audit_before_after_extracted(permission_resolver):
    org = _mkorg()
    _mkaudit(
        org,
        before={"name": "old"},
        after={"name": "new", "secret_key": "x"},
    )
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        q = OperationsQuery()
        rows = q.astrolift_audit_events(_info(), limit=10)
        row = rows[0]
        assert row.before == {"name": "old"}
        assert row.after["name"] == "new"
        assert row.after["secret_key"] == REDACTED_SENTINEL


def test_audit_date_range_filter_applies(permission_resolver):
    """``auto_now_add=True`` on AuditEvent.occurred_at means freshly
    inserted rows are stamped 'now' regardless of any constructor
    override (Django pre_save hook on the field). We exercise the
    filter by bracketing 'now' with bounds on either side: a far-past
    ``lte`` returns nothing, an ``lte`` slightly in the future returns
    the row, proving the resolver wires the parameter into the queryset.
    """
    org = _mkorg()
    now = timezone.now()
    _mkaudit(org, action="a.recent")
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        q = OperationsQuery()
        # Upper bound 5 days in the past — should exclude every freshly
        # inserted row.
        rows = q.astrolift_audit_events(
            _info(),
            created_at_lte=now - dt.timedelta(days=5),
        )
        assert [r.action for r in rows] == []

        # Lower bound 5 days in the past — should include the row.
        rows = q.astrolift_audit_events(
            _info(),
            created_at_gte=now - dt.timedelta(days=5),
        )
        assert "a.recent" in [r.action for r in rows]


def test_audit_page_returns_cursor(permission_resolver):
    org = _mkorg()
    for i in range(5):
        _mkaudit(org, action=f"a.{i}")
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        q = OperationsQuery()
        page1 = q.astrolift_audit_events_page(_info(), limit=2)
        assert len(page1.items) == 2
        assert page1.next_cursor is not None

        page2 = q.astrolift_audit_events_page(_info(), limit=2, after=page1.next_cursor)
        assert len(page2.items) == 2
        assert page2.next_cursor is not None

        page3 = q.astrolift_audit_events_page(_info(), limit=2, after=page2.next_cursor)
        assert len(page3.items) == 1
        assert page3.next_cursor is None


def test_audit_page_with_total_count(permission_resolver):
    org = _mkorg()
    for i in range(3):
        _mkaudit(org, action=f"a.{i}")
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        q = OperationsQuery()
        page = q.astrolift_audit_events_page(_info(), limit=10, include_total=True)
        assert page.total_count == 3


def test_audit_page_without_total_count_is_null(permission_resolver):
    org = _mkorg()
    _mkaudit(org)
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        q = OperationsQuery()
        page = q.astrolift_audit_events_page(_info(), limit=10)
        assert page.total_count is None


def test_retention_query_defaults_to_90(permission_resolver):
    org = _mkorg()
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        q = OperationsQuery()
        out = q.astrolift_audit_retention(_info())
        assert out.days >= 1
        assert out.days == 90


def test_retention_query_reflects_constance(permission_resolver):
    from constance import config

    org = _mkorg()
    permission_resolver.grant(Permission.AUDIT_LOG_READ)

    previous = config.AUDIT_RETENTION_DAYS
    config.AUDIT_RETENTION_DAYS = 365
    try:
        with tenant_context(TenantContext(organization_id=org.id)):
            q = OperationsQuery()
            out = q.astrolift_audit_retention(_info())
            assert out.days == 365
    finally:
        config.AUDIT_RETENTION_DAYS = previous


def test_audit_events_actor_filter(permission_resolver):
    org = _mkorg()
    AuditEvent.objects.create(
        organization=org,
        actor_kind="user",
        actor_id="99",
        action="a.x",
        decision="ALLOW",
        target_kind="",
        target_id="",
        target_slug="",
        data={},
    )
    AuditEvent.objects.create(
        organization=org,
        actor_kind="user",
        actor_id="100",
        action="a.y",
        decision="ALLOW",
        target_kind="",
        target_id="",
        target_slug="",
        data={},
    )
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        q = OperationsQuery()
        rows = q.astrolift_audit_events(_info(), actor_id="99")
        assert len(rows) == 1
        assert rows[0].actor_id == "99"
