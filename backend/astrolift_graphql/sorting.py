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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import strawberry
from django.db.models import F, OrderBy
from django.db.models.expressions import BaseExpression


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


# ---------------------------------------------------------------------------
# Multi-key sort for the list contract (spec 44 §5.1, #2149)
# ---------------------------------------------------------------------------
#
# The enum above serves cursor lists, where every order needs a seek clause
# of its own. Numbered lists page by OFFSET and can take any ordered set of
# declared keys, so they take the frontend's sort string instead:
# ``-deployed,name`` is "deployed newest first, then name A to Z", exactly
# what ``formatSort`` in ``components/list/list-state.ts`` writes and the
# URL carries. ``ListSortInput`` is the same thing as a typed list, for a
# list that would rather declare it that way; both parse to the same keys.


@strawberry.enum(name="AstroliftSortDirection")
class SortDirection(enum.Enum):
    ASC = "asc"
    DESC = "desc"


@strawberry.input(name="AstroliftListSortInput", description="One key of a multi-key list sort.")
class ListSortInput:
    key: str
    direction: SortDirection = SortDirection.ASC


@dataclass(frozen=True)
class SortKey:
    """One sort key a list declares: what it orders by, and where NULLs go.

    ``expr`` is a field path (``created_at``, ``project__name``) or any
    ORM expression (``Lower("name")``, a ``Subquery``). ``nulls_low``
    puts NULLs below every value, first ascending and last descending,
    the way the browser sorts "never deployed" before the oldest deploy;
    leave it unset on a NOT NULL column.
    """

    expr: str | BaseExpression
    nulls_low: bool = False

    def order(self, *, descending: bool) -> OrderBy:
        expr = F(self.expr) if isinstance(self.expr, str) else self.expr
        if descending:
            return expr.desc(nulls_last=True) if self.nulls_low else expr.desc()
        return expr.asc(nulls_first=True) if self.nulls_low else expr.asc()


def parse_sort_spec(spec: str | Sequence[ListSortInput] | None) -> list[tuple[str, bool]]:
    """``"-started,name"`` (or the typed list) as ``[(key, descending)]``.

    Mirrors ``parseSort`` in the frontend: blank parts and a bare ``-``
    are dropped. A key named twice keeps its first position, since only
    the first can decide anything.
    """
    if spec is None:
        return []
    if isinstance(spec, str):
        pairs = [
            (part[1:], True) if part.startswith("-") else (part, False)
            for part in (p.strip() for p in spec.split(","))
            if part and part != "-"
        ]
    else:
        pairs = [(item.key, item.direction is SortDirection.DESC) for item in spec]
    seen: set[str] = set()
    out: list[tuple[str, bool]] = []
    for key, descending in pairs:
        if key not in seen:
            seen.add(key)
            out.append((key, descending))
    return out


def resolve_list_sort(
    spec: str | Sequence[ListSortInput] | None,
    keys: Mapping[str, SortKey],
    *,
    default: str,
    tiebreak: str = "pk",
) -> list[OrderBy]:
    """The ORM ``order_by`` for a sort spec over a list's declared keys.

    An empty spec falls back to ``default`` (itself a spec string). An
    undeclared key raises :class:`UnsupportedSort` for the same reason
    ``resolve_sort`` refuses: a silently dropped key serves a list in an
    order nobody asked for. ``tiebreak`` is appended ascending so the
    order is total and a numbered page never repeats or skips a row.
    """
    pairs = parse_sort_spec(spec) or parse_sort_spec(default)
    unknown = [key for key, _ in pairs if key not in keys]
    if unknown:
        offered = ", ".join(sorted(keys))
        raise UnsupportedSort(f"sort {unknown[0]!r} is not available on this list; supported: {offered}")
    return [*(keys[key].order(descending=d) for key, d in pairs), F(tiebreak).asc()]
