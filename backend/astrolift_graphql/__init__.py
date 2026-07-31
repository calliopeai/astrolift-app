"""Astrolift GraphQL primitives shared across per-app schemas.

* ``GUID`` scalar — UUIDv7 string identifiers.
* ``MutationResultType`` — Strawberry binding of ``core.mutations.MutationResult``.
* ``PageType`` / ``keyset_page`` — cursor pagination for every list surface.

Per-app schemas (``astrolift_identity/schema/``,
``astrolift_registry/schema/``, …) compose into the root schema in
``config/schema.py``.
"""

from astrolift_graphql.errors import MutationErrorType
from astrolift_graphql.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    KeysetPage,
    PageType,
    clamp_limit,
    decode_cursor,
    encode_cursor,
    keyset_page,
    search_q,
)
from astrolift_graphql.results import MutationResultType, failure, success, version_mismatch
from astrolift_graphql.scalars import GUID

__all__ = [
    "DEFAULT_PAGE_LIMIT",
    "GUID",
    "MAX_PAGE_LIMIT",
    "KeysetPage",
    "MutationErrorType",
    "MutationResultType",
    "PageType",
    "clamp_limit",
    "decode_cursor",
    "encode_cursor",
    "failure",
    "keyset_page",
    "search_q",
    "success",
    "version_mismatch",
]
