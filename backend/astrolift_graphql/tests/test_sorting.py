"""Server-side sort for cursor-paginated lists (#1239).

The subtle half is not the ordering, it is the cursor. A cursor carries a sort
value and a tiebreak and nothing that says which column the first one came
from, so re-issuing it under a different order builds a seek clause comparing,
say, a timestamp against a name. The arity check passes. The page that comes
back is not visibly wrong; it is silently the wrong slice.
"""

from __future__ import annotations

import pytest

from astrolift_graphql.pagination import decode_cursor, encode_cursor
from astrolift_graphql.sorting import (
    DEFAULT_SORT,
    NAMED_MODEL_SORTS,
    ListSortKey,
    UnsupportedSort,
    resolve_sort,
)


def test_the_default_is_the_pre_existing_order():
    """Every one of these surfaces was newest-first before it gained a sort
    argument, and a default that changed the order for callers who never asked
    would be a silent behaviour change."""
    order, _ = resolve_sort(None, NAMED_MODEL_SORTS)

    assert DEFAULT_SORT is ListSortKey.CREATED_DESC
    assert order.sort_field == "created_at"
    assert order.descending is True


@pytest.mark.parametrize("key", list(ListSortKey))
def test_every_declared_order_names_a_not_null_column(key):
    """A NULL sorts to one end and compares as neither < nor =, so the seek
    stops matching and the walk truncates silently (#1235)."""
    order = NAMED_MODEL_SORTS[key]

    assert order.sort_field in {"created_at", "name"}
    assert order.tiebreak_field == "guid"


def test_an_unsupported_order_is_refused_rather_than_defaulted():
    """A silent fallback serves a correctly-paginated list in an order nobody
    asked for, which is indistinguishable from a broken sort control."""
    only_created = {ListSortKey.CREATED_DESC: NAMED_MODEL_SORTS[ListSortKey.CREATED_DESC]}

    with pytest.raises(UnsupportedSort, match="supported: created_desc"):
        resolve_sort(ListSortKey.NAME_ASC, only_created)


def test_each_order_gets_its_own_cursor_scope():
    """Two orders sharing a scope is the bug the scope exists to prevent."""
    scopes = {resolve_sort(key, NAMED_MODEL_SORTS)[1] for key in ListSortKey}

    assert len(scopes) == len(list(ListSortKey))


# ---- the cursor binding --------------------------------------------------------


def test_a_scoped_cursor_carries_its_order():
    token = encode_cursor("name_asc", "acme", "guid-1")

    assert decode_cursor(token, arity=3) == ("name_asc", "acme", "guid-1")


def test_an_unscoped_cursor_still_decodes_at_arity_two():
    """Fields that never gained a sort argument keep minting two-part cursors,
    so this must stay backwards compatible."""
    token = encode_cursor("2026-01-01T00:00:00+00:00", "guid-1")

    assert decode_cursor(token, arity=2) is not None
    assert decode_cursor(token, arity=3) is None


def test_a_cursor_from_another_order_does_not_decode_at_the_new_arity():
    """What makes the restart safe: the shapes differ, so a two-part cursor
    presented to a scoped field is rejected outright rather than unpacked."""
    unscoped = encode_cursor("acme", "guid-1")

    assert decode_cursor(unscoped, arity=3) is None


# ---- the surfaces that accept it ------------------------------------------------

SORTABLE_FIELDS = {
    "astroliftTeamsPage",
    "astroliftProjectsPage",
    "astroliftRolesPage",
    "astroliftPoliciesPage",
    "astroliftApiTokensPage",
    "astroliftAppDeployTokensPage",
}


def _schema_text() -> str:
    import pathlib

    for parent in pathlib.Path(__file__).resolve().parents:
        candidate = parent / "schema.graphql"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    pytest.skip("schema.graphql not found")
    raise AssertionError  # unreachable, keeps the type checker happy


def test_exactly_the_intended_fields_accept_a_sort():
    """Pinned rather than counted loosely.

    Every sortable field has to satisfy the NOT NULL sort column and not-null
    unique tiebreak that seek pagination needs, and that is a per-model check
    against the model, not something the enum can enforce. A field appearing
    here without that check is how the walk starts truncating silently.
    """
    text = _schema_text()
    accepting = {
        line.strip().split("(", 1)[0] for line in text.splitlines() if "sortBy: AstroliftListSortKey" in line
    }

    assert accepting == SORTABLE_FIELDS


def test_the_sort_argument_is_optional_everywhere():
    """A required argument would break every existing caller, and the default
    has to stay the order these lists already had."""
    text = _schema_text()

    for line in text.splitlines():
        if "sortBy: AstroliftListSortKey" in line:
            assert "sortBy: AstroliftListSortKey = null" in line, line.strip()
