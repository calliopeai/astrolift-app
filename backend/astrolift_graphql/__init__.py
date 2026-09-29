"""Astrolift GraphQL primitives shared across per-app schemas.

* ``GUID`` scalar — UUIDv7 string identifiers.
* ``MutationResultType`` — Strawberry binding of ``core.mutations.MutationResult``.
* ``PageType`` / ``keyset_page`` — cursor pagination for every list surface.
* ``numbered_page``, ``resolve_list_sort``, ``filter_q``, ``search_q``: the
  list contract (spec 44 §5.1); see ``README.md`` in this package.

Per-app schemas (``astrolift_identity/schema/``,
``astrolift_registry/schema/``, …) compose into the root schema in
``config/schema.py``.
"""

from astrolift_graphql.errors import MutationErrorType
from astrolift_graphql.filtering import FilterField, filter_q, filter_values
from astrolift_graphql.pagination import (
    DEFAULT_PAGE_LIMIT,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_LIMIT,
    KeysetPage,
    NumberedPage,
    PageType,
    clamp_limit,
    decode_cursor,
    encode_cursor,
    keyset_page,
    numbered_page,
    search_q,
)
from astrolift_graphql.results import MutationResultType, failure, success, version_mismatch
from astrolift_graphql.scalars import GUID
from astrolift_graphql.sorting import (
    ListSortInput,
    SortDirection,
    SortKey,
    UnsupportedSort,
    parse_sort_spec,
    resolve_list_sort,
)

__all__ = [
    "DEFAULT_PAGE_LIMIT",
    "DEFAULT_PAGE_SIZE",
    "GUID",
    "MAX_PAGE_LIMIT",
    "FilterField",
    "KeysetPage",
    "ListSortInput",
    "MutationErrorType",
    "MutationResultType",
    "NumberedPage",
    "PageType",
    "SortDirection",
    "SortKey",
    "UnsupportedSort",
    "clamp_limit",
    "decode_cursor",
    "encode_cursor",
    "failure",
    "filter_q",
    "filter_values",
    "keyset_page",
    "numbered_page",
    "parse_sort_spec",
    "resolve_list_sort",
    "search_q",
    "success",
    "version_mismatch",
]
