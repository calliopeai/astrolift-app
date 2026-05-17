"""Astrolift GraphQL primitives shared across per-app schemas.

* ``GUID`` scalar — base64-encoded ``Type:int`` global IDs.
* ``MutationResultType`` — Strawberry binding of ``core.mutations.MutationResult``.
* ``Connection``-style pagination helpers.

Per-app schemas (``astrolift_identity/schema/``,
``astrolift_registry/schema/``, …) compose into the root schema in
``config/schema.py``.
"""

from astrolift_graphql.errors import MutationErrorType
from astrolift_graphql.results import MutationResultType, failure, success, version_mismatch
from astrolift_graphql.scalars import GUID

__all__ = [
    "GUID",
    "MutationErrorType",
    "MutationResultType",
    "failure",
    "success",
    "version_mismatch",
]
