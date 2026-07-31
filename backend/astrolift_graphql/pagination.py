"""Keyset (cursor) pagination shared across every per-app GraphQL schema.

Six resolvers hand-rolled structurally identical keyset walks before this
module existed (#1235); every one repeated the same four steps — clamp the
limit, decode the cursor into a seek ``Q``, overfetch one row to detect
end-of-stream, re-encode the last row as the next cursor. The duplication
is why 35 other list resolvers shipped with a hard cap instead: writing the
walk by hand was expensive enough that "slice at 500" always won.

``keyset_page`` collapses the walk to one call. A conversion is:

    page = keyset_page(qs, cursor=after, limit=limit)
    return page.map(deployment_to_type)

Seek key
--------
The default key is ``(-created_at, -guid)``. ``guid`` is a UUIDv7 on every
modern ``BaseCoreModel`` (``core/fields/uuid_v7.py``) so the tiebreak is
itself time-ordered and rows never churn between pages. Models that predate
that base — or that order on a different column — override ``sort_field`` /
``tiebreak_field``; both accept ``__``-joined relation paths.

Two invariants make the walk correct, and both are enforced here rather
than left to the caller:

* The ORDER BY is derived from the seek key, not supplied by the caller. A
  queryset whose ordering disagrees with its cursor silently skips and
  repeats rows, which is the single most common keyset bug.
* ``sort_field`` must be NOT NULL. NULLs sort to one end and compare as
  neither ``<`` nor ``=``, so a NULL row terminates the walk early and
  every row behind it becomes unreachable. Sort on a non-null column and
  filter or coalesce the nullable one instead.

Cursor format
-------------
Unpadded urlsafe base64 of compact JSON ``[sort_value, tiebreak_value]``,
byte-identical to the ``Event`` codec this generalises, so cursors already
issued by ``astroliftEventsPage`` / ``astroliftRecentActivity`` /
``astroliftAuditEventsPage`` keep decoding across the cutover.

Values ride as strings and go back to the ORM as strings; Django's field
``get_prep_value`` coerces them (ISO-8601 → datetime, hex → UUID, digits →
int) on the way into the WHERE clause. That is what keeps the helper
model-agnostic: it never needs to know a column's Python type.

A malformed cursor decodes to ``None`` and restarts from the top rather
than raising. A stale cursor is a UX event (a bookmarked URL, a sort
change mid-walk), not a client error worth a 400.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

import strawberry
from django.db.models import Q, QuerySet

# ``from __future__ import annotations`` turns every annotation into a
# string, and Strawberry resolves the ones on a GraphQL type against this
# module's globals. The PEP 695 parameter on ``PageType[T]`` is not visible
# there, so the name has to exist at module scope too — same shim as
# ``results.py`` carries for ``MutationResultType``.
T = TypeVar("T")
M = TypeVar("M")

__all__ = [
    "DEFAULT_PAGE_LIMIT",
    "MAX_PAGE_LIMIT",
    "KeysetPage",
    "PageType",
    "clamp_limit",
    "decode_cursor",
    "encode_cursor",
    "keyset_page",
    "search_q",
]

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200


# ---------------------------------------------------------------------------
# Cursor codec
# ---------------------------------------------------------------------------


def _cursor_value(value: Any) -> str | None:
    """Render a seek-key component as the string that goes in the token.

    ``isoformat`` keeps microsecond precision, which the walk depends on:
    rows written inside the same second are extremely common (a bulk
    import, a fan-out of deploy events) and truncating to seconds would
    make the tiebreak carry the whole ordering.

    ``None`` stays ``None`` rather than becoming the string ``"None"`` —
    stringifying it would mint a cursor that compares against literal
    text and quietly returns the wrong rows.
    """
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value)


def encode_cursor(*values: Any) -> str:
    """Opaque token encoding one keyset position."""
    rendered: list[str | None] = [_cursor_value(v) for v in values]
    payload = json.dumps(rendered, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode()


def decode_cursor(token: str, *, arity: int = 2) -> tuple[str, ...] | None:
    """Return the token's components, or ``None`` when it is unusable.

    Garbage is swallowed on purpose — see the module docstring. ``arity``
    is checked so a cursor minted for a differently-shaped key (a resolver
    that gained a tiebreak column) restarts instead of building a seek
    clause out of the wrong values.
    """
    pad = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + pad)
        parts = json.loads(raw)
    except (binascii.Error, ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(parts, list) or len(parts) != arity:
        return None
    if any(part is None for part in parts):
        return None
    return tuple(str(part) for part in parts)


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


def clamp_limit(
    limit: int | None,
    *,
    default: int = DEFAULT_PAGE_LIMIT,
    maximum: int = MAX_PAGE_LIMIT,
) -> int:
    """Coerce a client-supplied page size into ``[1, maximum]``.

    ``None`` and non-positive values fall back to ``default`` rather than
    erroring; GraphQL ``Int`` is signed, so ``limit: -1`` is reachable from
    any client and used to produce an empty slice.
    """
    if limit is None or limit <= 0:
        return min(default, maximum)
    return min(limit, maximum)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def search_q(term: str, *fields: str) -> Q:
    """OR of ``icontains`` over ``fields`` for a free-text search box.

    Deliberately un-tokenised, matching the app-list filter this
    generalises: operators type a fragment of one identifier ("prod-api",
    "ana@"), not a multi-word phrase, and splitting on whitespace would
    make a trailing space change the result set.
    """
    query = Q()
    for field in fields:
        query |= Q(**{f"{field}__icontains": term})
    return query


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------


def _resolve_path(row: Any, path: str) -> Any:
    """Read ``a__b__c`` off a model instance as ``row.a.b.c``."""
    value = row
    for part in path.split("__"):
        value = getattr(value, part)
    return value


def _seek(
    *,
    sort_field: str,
    tiebreak_field: str,
    sort_value: str,
    tiebreak_value: str,
    descending: bool,
) -> Q:
    """WHERE clause for "strictly after this position in the sort order"."""
    op = "lt" if descending else "gt"
    return Q(**{f"{sort_field}__{op}": sort_value}) | (
        Q(**{sort_field: sort_value}) & Q(**{f"{tiebreak_field}__{op}": tiebreak_value})
    )


@dataclass(frozen=True)
class KeysetPage[M]:
    """One page of model rows plus the token that reaches the next one.

    ``next_cursor is None`` means the walk is exhausted — it is derived
    from an overfetched row, never from ``len(rows) < limit``, so a page
    that happens to land exactly on the last row still terminates.
    """

    rows: list[M]
    next_cursor: str | None
    total_count: int | None

    def map[T](self, to_type: Callable[[M], T]) -> PageType[T]:
        """Project the rows through their GraphQL type mapper."""
        return PageType(
            items=[to_type(row) for row in self.rows],
            next_cursor=self.next_cursor,
            total_count=self.total_count,
        )

    @classmethod
    def empty(cls, *, with_total: bool = True) -> KeysetPage[M]:
        """The deny-by-default page.

        Read resolvers over org-owned rows return this when there is no
        tenant context — never an unscoped queryset (#1042 / #1183).
        """
        return cls(rows=[], next_cursor=None, total_count=0 if with_total else None)


@strawberry.type(name="Page", description="One page of a cursor-paginated list.")
class PageType[T]:
    items: list[T]
    next_cursor: str | None = strawberry.field(
        default=None,
        description="Opaque token for the next page; null when the list is exhausted.",
    )
    total_count: int | None = strawberry.field(
        default=None,
        description="Total rows matching the filters, across all pages.",
    )


def keyset_page[M](
    qs: QuerySet[M],
    *,
    cursor: str | None = None,
    limit: int | None = None,
    default_limit: int = DEFAULT_PAGE_LIMIT,
    max_limit: int = MAX_PAGE_LIMIT,
    sort_field: str = "created_at",
    tiebreak_field: str = "guid",
    descending: bool = True,
    with_total: bool = True,
) -> KeysetPage[M]:
    """Walk ``qs`` one keyset page at a time.

    ``qs`` arrives filtered (org scope, search, status facets) and
    *unordered* — the ordering is imposed here so it cannot drift from the
    seek key. See the module docstring for the two invariants.

    ``with_total`` costs one extra ``COUNT(*)`` over the filtered set,
    evaluated before the seek so it reports the whole result set rather
    than the tail. Tables render "N results" from it; pass ``False`` for
    high-volume streams where the count is not worth the scan.
    """
    page_size = clamp_limit(limit, default=default_limit, maximum=max_limit)
    total = qs.count() if with_total else None

    direction = "-" if descending else ""
    qs = qs.order_by(f"{direction}{sort_field}", f"{direction}{tiebreak_field}")

    if cursor:
        decoded = decode_cursor(cursor, arity=2)
        if decoded is not None:
            sort_value, tiebreak_value = decoded
            qs = qs.filter(
                _seek(
                    sort_field=sort_field,
                    tiebreak_field=tiebreak_field,
                    sort_value=sort_value,
                    tiebreak_value=tiebreak_value,
                    descending=descending,
                )
            )

    # Overfetch one row: the only honest end-of-stream signal.
    fetched: Sequence[M] = list(qs[: page_size + 1])
    rows = list(fetched[:page_size])

    next_cursor = None
    if len(fetched) > page_size and rows:
        sort_value = _resolve_path(rows[-1], sort_field)
        tiebreak_value = _resolve_path(rows[-1], tiebreak_field)
        # A NULL in the seek key means the caller broke the not-null
        # invariant. Stopping the walk loses the tail; minting a cursor
        # anyway would hand the client one that decodes to nothing and
        # re-serves page one forever. Truncating is the recoverable half.
        if sort_value is not None and tiebreak_value is not None:
            next_cursor = encode_cursor(sort_value, tiebreak_value)

    return KeysetPage(rows=rows, next_cursor=next_cursor, total_count=total)
