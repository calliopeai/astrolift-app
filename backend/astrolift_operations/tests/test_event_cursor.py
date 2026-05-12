"""Tests for Event cursor pagination (#43)."""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace

import pytest
from django.utils import timezone

from astrolift_identity.models import Organization
from astrolift_operations.models import Event
from astrolift_operations.schema.queries import (
    OperationsQuery,
    _decode_event_cursor,
    _encode_event_cursor,
)
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    """Stand-in for strawberry.types.Info — the resolvers only read
    .context.user, and the @tenant_scoped guard is satisfied by the
    contextvar set via the ``tenant_context`` context manager."""
    return SimpleNamespace(context=SimpleNamespace(user=None))


def _mkorg() -> Organization:
    return Organization.objects.create(name="CursorTest", slug="cursor-test")


def _mkevent(org: Organization, n: int) -> Event:
    """Make an event N seconds in the past so ordering is deterministic.

    bulk_create bypasses auto_now_add so we can write occurred_at
    directly — and the append-only trigger only refuses UPDATE/DELETE,
    not INSERT.
    """
    return Event.objects.bulk_create(
        [
            Event(
                event_type="test.event",
                payload={"n": n},
                organization=org,
                occurred_at=timezone.now() - dt.timedelta(seconds=n),
            )
        ]
    )[0]


def test_cursor_round_trips_through_decode():
    now = timezone.now()
    guid = str(uuid.uuid4())
    token = _encode_event_cursor(now, guid)
    decoded = _decode_event_cursor(token)
    assert decoded is not None
    assert decoded[0].isoformat() == now.isoformat()
    assert decoded[1] == guid


def test_decode_returns_none_for_garbage():
    assert _decode_event_cursor("not-base64!!!") is None
    assert _decode_event_cursor("") is None


def test_pagination_walks_all_events_with_no_overlap(permission_resolver):
    org = _mkorg()
    from core.permissions import Permission

    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        events = [_mkevent(org, n) for n in range(1, 8)]
        expected_ids = [str(e.guid) for e in sorted(events, key=lambda e: e.occurred_at, reverse=True)]

        q = OperationsQuery()
        page1 = q.astrolift_events_page(_info(), limit=3)
        assert len(page1.items) == 3
        assert page1.next_cursor is not None

        page2 = q.astrolift_events_page(_info(), limit=3, after=page1.next_cursor)
        assert len(page2.items) == 3
        assert page2.next_cursor is not None

        page3 = q.astrolift_events_page(_info(), limit=3, after=page2.next_cursor)
        assert len(page3.items) == 1
        assert page3.next_cursor is None  # end of stream

        seen = [str(i.id) for i in page1.items + page2.items + page3.items]
        assert seen == expected_ids, "pagination drifted; rows duplicated or skipped"


def test_pagination_respects_event_type_filter(permission_resolver):
    org = _mkorg()
    from core.permissions import Permission

    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        for n in range(1, 4):
            _mkevent(org, n)
        Event.objects.create(event_type="other.kind", payload={}, organization=org)

        q = OperationsQuery()
        page = q.astrolift_events_page(_info(), limit=10, event_type="test.event")
        assert len(page.items) == 3
        assert all(i.event_type == "test.event" for i in page.items)
