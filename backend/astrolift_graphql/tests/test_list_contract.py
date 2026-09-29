"""The list contract helpers (spec 44 §5.1, #2149): numbered pages, the
multi-key sort spec, declared filters and prefix search.

The numbered page and the prefix search run against real Postgres on
``Event``, as the keyset tests do: OFFSET correctness and a UUID compared
by its text form only mean anything against the real backend.
"""

from __future__ import annotations

import datetime as dt
import enum
from dataclasses import dataclass

import pytest
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.utils import timezone

from astrolift_graphql import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_LIMIT,
    FilterField,
    ListSortInput,
    SortDirection,
    SortKey,
    UnsupportedSort,
    filter_q,
    filter_values,
    numbered_page,
    parse_sort_spec,
    resolve_list_sort,
    search_q,
)
from astrolift_identity.models import Organization
from astrolift_operations.models import Event

# ---------------------------------------------------------------------------
# Sort spec
# ---------------------------------------------------------------------------


def test_the_sort_string_is_the_frontends_format_sort():
    """``formatSort`` writes ``-started,name``; ``parseSort`` drops blanks and a bare dash."""
    assert parse_sort_spec("-started,,name,-") == [("started", True), ("name", False)]
    assert parse_sort_spec(" -a , b ") == [("a", True), ("b", False)]
    assert parse_sort_spec(None) == []
    assert parse_sort_spec("") == []


def test_the_typed_sort_input_parses_the_same():
    typed = [ListSortInput(key="started", direction=SortDirection.DESC), ListSortInput(key="name")]
    assert parse_sort_spec(typed) == parse_sort_spec("-started,name")


def test_a_repeated_key_keeps_its_first_position():
    assert parse_sort_spec("name,-created,-name") == [("name", False), ("created", True)]


KEYS = {
    "name": SortKey(Lower("name")),
    "created": SortKey("created_at"),
    "deployed": SortKey("deployed_at", nulls_low=True),
}


def test_resolve_appends_the_pk_tiebreak():
    order = resolve_list_sort("-created", KEYS, default="name")
    assert order == [F("created_at").desc(), F("pk").asc()]


def test_resolve_falls_back_to_the_default_spec():
    assert resolve_list_sort(None, KEYS, default="-created") == resolve_list_sort(
        "-created", KEYS, default="name"
    )
    assert resolve_list_sort("", KEYS, default="-created")[0] == F("created_at").desc()


def test_nulls_low_puts_nulls_first_ascending_and_last_descending():
    asc, _ = resolve_list_sort("deployed", KEYS, default="name")
    desc, _ = resolve_list_sort("-deployed", KEYS, default="name")
    assert asc == F("deployed_at").asc(nulls_first=True)
    assert desc == F("deployed_at").desc(nulls_last=True)


def test_an_undeclared_key_is_refused_not_dropped():
    with pytest.raises(UnsupportedSort, match="'owner'"):
        resolve_list_sort("name,-owner", KEYS, default="name")


def test_a_custom_tiebreak():
    assert resolve_list_sort("name", KEYS, default="name", tiebreak="guid")[-1] == F("guid").asc()


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class Colour(enum.Enum):
    RED = "red"
    BLUE = "blue"


@dataclass
class FakeFilter:
    """Stands in for a strawberry input, which is a dataclass."""

    status: list[Colour] | None = None
    project: str | None = None
    owner: str | None = None
    tags: list[str] | None = None
    archived: bool | None = None


FIELDS = {
    "status": FilterField("status"),
    "project": FilterField("project__slug"),
    "owner": FilterField("created_by_id", me=True),
    "tags": FilterField(q=lambda v: Q(tags__overlap=v)),
}


def test_unset_and_empty_values_do_not_filter():
    assert filter_values(None) == {}
    assert filter_values(FakeFilter(status=[], tags=None)) == {}
    assert filter_q(FakeFilter(), FIELDS) == Q()


def test_enums_unwrap_and_lists_become_in():
    q = filter_q(FakeFilter(status=[Colour.RED, Colour.BLUE], project="demo"), FIELDS)
    assert q == Q(status__in=["red", "blue"]) & Q(project__slug="demo")


def test_a_mapping_works_like_an_input():
    assert filter_q({"project": "demo"}, FIELDS) == Q(project__slug="demo")


def test_a_custom_q_field_gets_the_value():
    assert filter_q(FakeFilter(tags=["a"]), FIELDS) == Q(tags__overlap=["a"])


def test_me_resolves_to_the_viewer_and_denies_without_one():
    assert filter_q(FakeFilter(owner="me"), FIELDS, me=7) == Q(created_by_id=7)
    assert filter_q(FakeFilter(owner="me"), FIELDS) == Q(pk__in=[])
    assert filter_q(FakeFilter(owner="12"), FIELDS) == Q(created_by_id="12")


def test_fields_missing_from_the_table_are_left_to_the_resolver():
    assert filter_q(FakeFilter(archived=True), FIELDS) == Q()
    assert filter_values(FakeFilter(archived=False)) == {"archived": False}


# ---------------------------------------------------------------------------
# Numbered pages and search, on real rows
# ---------------------------------------------------------------------------


def _events(count: int) -> tuple[Organization, list[Event]]:
    org = Organization.objects.create(name="Numbered", slug="numbered-2149")
    now = timezone.now()
    events = Event.objects.bulk_create(
        [
            Event(
                event_type="t.e",
                payload={"n": n},
                organization=org,
                occurred_at=now - dt.timedelta(seconds=n),
            )
            for n in range(count)
        ]
    )
    return org, events


@pytest.mark.django_db
def test_numbered_pages_cover_every_row_once():
    org, events = _events(7)
    qs = Event.objects.filter(organization=org)
    order = [F("event_type").asc(), F("pk").desc()]

    pages = [numbered_page(qs, order_by=order, page=n, page_size=3) for n in (1, 2, 3)]

    seen = [e.pk for p in pages for e in p.rows]
    assert seen == sorted((e.pk for e in events), reverse=True)
    assert [len(p.rows) for p in pages] == [3, 3, 1]
    assert {p.total_count for p in pages} == {7}
    assert pages[0].page_count == 3


@pytest.mark.django_db
def test_numbered_page_defaults_and_clamps():
    org, _ = _events(2)
    qs = Event.objects.filter(organization=org)
    order = [F("pk").asc()]

    first = numbered_page(qs, order_by=order, page=0, page_size=-5)
    huge = numbered_page(qs, order_by=order, page_size=10_000)
    past = numbered_page(qs, order_by=order, page=5, page_size=2)

    assert (first.page, first.page_size) == (1, DEFAULT_PAGE_SIZE)
    assert huge.page_size == MAX_PAGE_LIMIT
    assert (past.rows, past.page, past.total_count) == ([], 5, 2)


@pytest.mark.django_db
def test_map_projects_a_numbered_page():
    org, events = _events(3)
    page = numbered_page(Event.objects.filter(organization=org), order_by=[F("pk").asc()], page_size=2)
    typed = page.map(lambda e: e.pk)
    assert typed.items == [events[0].pk, events[1].pk]
    assert (typed.total_count, typed.page, typed.page_size, typed.next_cursor) == (3, 1, 2, None)


@pytest.mark.django_db
def test_search_prefix_matches_a_uuid_by_its_text_form():
    org, events = _events(3)
    target = events[1]
    qs = Event.objects.filter(organization=org)
    token = str(target.guid)[:28]

    assert list(qs.filter(search_q(token, "event_type", prefix=("guid",)))) == [target]
    # A prefix field does not match from the middle.
    assert not qs.filter(search_q(str(target.guid)[5:28], prefix=("guid",))).exists()
