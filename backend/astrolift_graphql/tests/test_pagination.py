"""Tests for the shared keyset pagination helper (#1235).

The helper is the single point of failure for every converted list
surface, so these lean on real rows in Postgres rather than a fake
queryset: the seek clause has to survive Django's field coercion (ISO
string → timestamptz, hex → uuid, digits → int), and that only happens
against a real backend.

``Event`` is the model under test because it is the one target whose
``occurred_at`` can be written directly (``bulk_create`` bypasses
``auto_now_add``), which is what makes deterministic ordering possible.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import uuid

import pytest
from django.utils import timezone

from astrolift_graphql.pagination import (
    MAX_PAGE_LIMIT,
    KeysetPage,
    clamp_limit,
    decode_cursor,
    encode_cursor,
    keyset_page,
    search_q,
)
from astrolift_identity.models import Organization
from astrolift_operations.models import Event

pytestmark = pytest.mark.django_db


def _mkorg(slug: str = "keyset-test") -> Organization:
    return Organization.objects.create(name="KeysetTest", slug=slug)


def _mkevents(org: Organization, count: int, *, event_type: str = "test.event") -> list[Event]:
    """Events one second apart, newest first, so ordering is total."""
    now = timezone.now()
    return Event.objects.bulk_create(
        [
            Event(
                event_type=event_type,
                payload={"n": n},
                organization=org,
                occurred_at=now - dt.timedelta(seconds=n),
            )
            for n in range(count)
        ]
    )


# ---------------------------------------------------------------------------
# Codec
# ---------------------------------------------------------------------------


def test_cursor_round_trips_preserving_microseconds():
    now = timezone.now().replace(microsecond=123456)
    guid = str(uuid.uuid4())
    decoded = decode_cursor(encode_cursor(now, guid))
    assert decoded == (now.isoformat(), guid)


def test_decode_returns_none_for_unusable_tokens():
    assert decode_cursor("not-base64!!!") is None
    assert decode_cursor("") is None
    # Valid base64-JSON, wrong arity — a cursor minted for a differently
    # shaped seek key must restart, not build a bogus WHERE clause.
    assert decode_cursor(encode_cursor("a", "b", "c")) is None
    assert decode_cursor(encode_cursor("solo")) is None


def test_decode_rejects_null_components():
    """A null component would produce ``field__lt=None`` — a TypeError at
    the ORM boundary rather than a restart."""
    assert decode_cursor(encode_cursor(None, "b")) is None


def test_encode_keeps_none_distinct_from_the_string_none():
    """Stringifying ``None`` would mint a cursor that compares against the
    literal text "None" and quietly returns the wrong rows."""
    assert decode_cursor(encode_cursor("None", "b")) == ("None", "b")
    assert decode_cursor(encode_cursor(None, "b")) is None


def test_null_seek_value_terminates_rather_than_looping():
    """``sort_field`` is documented NOT NULL. When a caller breaks that,
    the walk must stop — handing back a cursor that decodes to nothing
    would re-serve page one forever."""
    org = _mkorg()
    Event.objects.bulk_create(
        [Event(event_type="test.event", payload={}, organization=org, registered_app=None) for _ in range(4)]
    )
    page = keyset_page(
        Event.objects.filter(organization=org),
        limit=2,
        sort_field="registered_app",  # nullable, and null on every row here
        tiebreak_field="guid",
    )
    assert len(page.rows) == 2
    assert page.next_cursor is None


def test_cursor_format_matches_the_event_codec_it_generalises():
    """Cursors already issued by the operations resolvers keep working.

    The ``Event`` codec this generalises (``_encode_event_cursor``,
    deleted when ``astroliftEventsPage`` / ``astroliftRecentActivity``
    / ``astroliftAuditEventsPage`` moved onto ``keyset_page``) emitted
    unpadded urlsafe base64 of compact JSON
    ``[occurred_at_iso, guid_str]``. Its exact bytes are reconstructed
    here rather than compared against the deleted function: a bookmark
    or an in-flight "load more" holds a token minted before the
    cutover, and a format change would silently restart those walks
    from the top instead of erroring.
    """
    now = timezone.now()
    guid = str(uuid.uuid4())
    legacy = (
        base64.urlsafe_b64encode(json.dumps([now.isoformat(), guid], separators=(",", ":")).encode())
        .rstrip(b"=")
        .decode()
    )
    assert encode_cursor(now, guid) == legacy


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        (None, 50),
        (0, 50),
        (-1, 50),
        (10, 10),
        (10_000, MAX_PAGE_LIMIT),
    ],
)
def test_clamp_limit(supplied, expected):
    assert clamp_limit(supplied) == expected


def test_clamp_limit_default_never_exceeds_maximum():
    assert clamp_limit(None, default=500, maximum=25) == 25


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------


def test_walk_covers_every_row_exactly_once():
    org = _mkorg()
    _mkevents(org, 7)
    expected = [
        str(g)
        for g in Event.objects.filter(organization=org)
        .order_by("-occurred_at", "-guid")
        .values_list("guid", flat=True)
    ]

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):  # bounded so a non-terminating walk fails loudly
        page = keyset_page(
            Event.objects.filter(organization=org),
            cursor=cursor,
            limit=3,
            sort_field="occurred_at",
        )
        seen.extend(str(row.guid) for row in page.rows)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert cursor is None, "walk did not terminate"
    assert seen == expected, "pagination drifted; rows duplicated or skipped"


def test_walk_terminates_when_the_last_page_is_exactly_full():
    """``next_cursor`` comes from the overfetched row, not from
    ``len(rows) < limit`` — six rows at three per page must end after two
    pages, not hand out a third, empty one."""
    org = _mkorg()
    _mkevents(org, 6)

    page1 = keyset_page(Event.objects.filter(organization=org), limit=3, sort_field="occurred_at")
    page2 = keyset_page(
        Event.objects.filter(organization=org),
        cursor=page1.next_cursor,
        limit=3,
        sort_field="occurred_at",
    )
    assert len(page2.rows) == 3
    assert page2.next_cursor is None


def test_rows_sharing_a_sort_value_are_split_by_the_tiebreak():
    """The whole reason for a composite key: bulk-written rows land on an
    identical timestamp, and a timestamp-only seek would either skip the
    rest of the second or loop on it forever."""
    org = _mkorg()
    same_instant = timezone.now()
    Event.objects.bulk_create(
        [
            Event(
                event_type="test.event",
                payload={"n": n},
                organization=org,
                occurred_at=same_instant,
            )
            for n in range(5)
        ]
    )

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        page = keyset_page(
            Event.objects.filter(organization=org),
            cursor=cursor,
            limit=2,
            sort_field="occurred_at",
        )
        seen.extend(str(row.guid) for row in page.rows)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert cursor is None, "walk did not terminate"
    assert len(seen) == 5
    assert len(set(seen)) == 5, "tiebreak failed; a row was served twice"


def test_ascending_walk_reverses_both_order_and_seek():
    org = _mkorg()
    _mkevents(org, 5)
    expected = [
        str(g)
        for g in Event.objects.filter(organization=org)
        .order_by("occurred_at", "guid")
        .values_list("guid", flat=True)
    ]

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        page = keyset_page(
            Event.objects.filter(organization=org),
            cursor=cursor,
            limit=2,
            sort_field="occurred_at",
            descending=False,
        )
        seen.extend(str(row.guid) for row in page.rows)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert seen == expected


def test_caller_supplied_ordering_cannot_desync_the_seek_key():
    """A queryset ordered against its cursor is the classic keyset bug;
    the helper imposes the ORDER BY so the mistake is unreachable."""
    org = _mkorg()
    _mkevents(org, 5)

    page = keyset_page(
        Event.objects.filter(organization=org).order_by("occurred_at"),  # wrong way round
        limit=2,
        sort_field="occurred_at",
    )
    occurred = [row.occurred_at for row in page.rows]
    assert occurred == sorted(occurred, reverse=True)


def test_total_count_spans_the_whole_result_set_not_the_page():
    org = _mkorg()
    _mkevents(org, 7)

    page = keyset_page(Event.objects.filter(organization=org), limit=3, sort_field="occurred_at")
    assert len(page.rows) == 3
    assert page.total_count == 7

    tail = keyset_page(
        Event.objects.filter(organization=org),
        cursor=page.next_cursor,
        limit=3,
        sort_field="occurred_at",
    )
    assert tail.total_count == 7, "count must not shrink as the walk advances"


def test_total_count_is_skippable():
    org = _mkorg()
    _mkevents(org, 3)
    page = keyset_page(
        Event.objects.filter(organization=org),
        limit=2,
        sort_field="occurred_at",
        with_total=False,
    )
    assert page.total_count is None


def test_filters_apply_to_both_the_page_and_the_count():
    org = _mkorg()
    _mkevents(org, 4, event_type="test.event")
    _mkevents(org, 3, event_type="other.kind")

    page = keyset_page(
        Event.objects.filter(organization=org, event_type="test.event"),
        limit=10,
        sort_field="occurred_at",
    )
    assert page.total_count == 4
    assert {row.event_type for row in page.rows} == {"test.event"}


def test_garbage_cursor_restarts_from_the_top():
    org = _mkorg()
    _mkevents(org, 4)

    fresh = keyset_page(Event.objects.filter(organization=org), limit=2, sort_field="occurred_at")
    garbage = keyset_page(
        Event.objects.filter(organization=org),
        cursor="!!!not-a-cursor!!!",
        limit=2,
        sort_field="occurred_at",
    )
    assert [r.guid for r in garbage.rows] == [r.guid for r in fresh.rows]


def test_empty_queryset_yields_an_exhausted_page():
    org = _mkorg()
    page = keyset_page(Event.objects.filter(organization=org), limit=10, sort_field="occurred_at")
    assert page.rows == []
    assert page.next_cursor is None
    assert page.total_count == 0


def test_limit_is_clamped_inside_the_walk():
    org = _mkorg()
    _mkevents(org, 12)
    page = keyset_page(
        Event.objects.filter(organization=org),
        limit=10_000,
        max_limit=5,
        sort_field="occurred_at",
    )
    assert len(page.rows) == 5


def test_empty_page_is_the_deny_by_default_shape():
    page: KeysetPage[Event] = KeysetPage.empty()
    assert page.rows == []
    assert page.next_cursor is None
    assert page.total_count == 0
    assert KeysetPage.empty(with_total=False).total_count is None


def test_map_projects_rows_and_carries_the_envelope():
    org = _mkorg()
    _mkevents(org, 3)
    page = keyset_page(Event.objects.filter(organization=org), limit=2, sort_field="occurred_at")
    projected = page.map(lambda row: row.event_type)

    assert projected.items == ["test.event", "test.event"]
    assert projected.next_cursor == page.next_cursor
    assert projected.total_count == 3


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_q_ors_icontains_across_fields():
    org = _mkorg()
    Event.objects.bulk_create(
        [
            Event(event_type="deploy.started", payload={}, organization=org),
            Event(event_type="secret.rotated", payload={}, organization=org),
        ]
    )
    matched = Event.objects.filter(organization=org).filter(search_q("DEPLOY", "event_type"))
    assert [e.event_type for e in matched] == ["deploy.started"]


def test_search_q_with_no_fields_matches_everything():
    """An empty ``Q()`` is the identity filter, so a caller that passes no
    searchable fields degrades to "no filter" rather than "no rows"."""
    org = _mkorg()
    _mkevents(org, 3)
    assert Event.objects.filter(organization=org).filter(search_q("anything")).count() == 3
