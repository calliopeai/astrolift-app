"""
GraphQL scalar types.

The platform exposes only ``guid`` strings to clients. Internal
integer PKs never leak through the API. ``GUID`` is a thin
NewType-style scalar so type-checkers can distinguish it from raw
strings at call sites.
"""

from __future__ import annotations

from typing import Any, NewType

import strawberry

GUID = strawberry.scalar(
    NewType("GUID", str),
    description="UUID v7 — the platform's external identifier.",
    serialize=lambda v: str(v),
    parse_value=lambda v: str(v),
)


def to_guid(value: Any) -> str:
    """Coerce a model.guid (UUID) to the wire string."""
    return str(value)
