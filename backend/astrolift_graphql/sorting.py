"""Sort orders for cursor-paginated list fields (#1239).

Waves 1-3 of #1230 deleted client-side sort from several tables rather than
keeping it. That was right: reordering the 25 rows in hand while the other 375
sit on the server produces an order that is wrong at every page boundary, and a
control that looks authoritative and is not is worse than no control.

Bringing sort back means bringing it to the server, and a seek-paginated field
can only sort on a column that satisfies two constraints (#1235):

*The sort column must be NOT NULL.* A NULL sorts to one end and compares as
neither ``<`` nor ``=``, so the seek clause stops matching and the walk
truncates silently. This is why ``workflowDefinitionsPage`` sorts on a coalesced
name rather than a nullable column.

*It needs a not-null unique tiebreak.* Without one, rows sharing a sort value
straddle a page boundary and are served twice or skipped.

Declaring orders in a table rather than building them per resolver keeps both
constraints checkable in one place, and keeps the cursor scope, which is what
makes a sort change safe, from being something each field has to remember.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

import strawberry


@strawberry.enum(name="AstroliftListSortKey")
class ListSortKey(enum.Enum):
    """Sort axes shared by the simple list surfaces.

    Deliberately small. Every axis here has to hold for every field that
    accepts the enum, and a per-field axis belongs in a per-field enum rather
    than here where it would be offered on surfaces that cannot honour it.
    """

    CREATED_DESC = "created_desc"
    CREATED_ASC = "created_asc"
    NAME_ASC = "name_asc"
    NAME_DESC = "name_desc"


@dataclass(frozen=True)
class SortOrder:
    """One order, as the arguments ``keyset_page`` already accepts."""

    sort_field: str
    descending: bool
    tiebreak_field: str = "guid"


#: The orders every ``name``-bearing model can honour. A model whose ``name`` is
#: nullable must not use this table; the NOT NULL invariant is the caller's to
#: keep and a NULL truncates the walk rather than failing loudly.
NAMED_MODEL_SORTS: dict[ListSortKey, SortOrder] = {
    ListSortKey.CREATED_DESC: SortOrder("created_at", descending=True),
    ListSortKey.CREATED_ASC: SortOrder("created_at", descending=False),
    ListSortKey.NAME_ASC: SortOrder("name", descending=False),
    ListSortKey.NAME_DESC: SortOrder("name", descending=True),
}

DEFAULT_SORT = ListSortKey.CREATED_DESC


class UnsupportedSort(ValueError):
    """A field was asked for an order it cannot serve."""


def resolve_sort(
    sort_by: ListSortKey | None,
    supported: dict[ListSortKey, SortOrder],
) -> tuple[SortOrder, str]:
    """Return the order and the cursor scope that binds a cursor to it.

    Refusing an unsupported order rather than falling back to the default is
    the point: a silent fallback serves a correctly-paginated list in an order
    the operator did not ask for, which looks like the sort control is broken
    and is indistinguishable from data that happens to be in that order.
    """
    key = sort_by or DEFAULT_SORT
    try:
        return supported[key], key.value
    except KeyError:
        offered = ", ".join(sorted(k.value for k in supported))
        raise UnsupportedSort(
            f"sort {key.value!r} is not available on this list; supported: {offered}"
        ) from None
