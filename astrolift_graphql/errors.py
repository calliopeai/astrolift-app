"""
Strawberry binding for the structured error envelope.

Mirrors ``core.mutations.MutationError``. Distinct module so
``@strawberry.type`` can decorate a frozen dataclass-style class
without colliding with the runtime ``MutationError`` defined in core.
"""

from __future__ import annotations

import strawberry


@strawberry.type(name="MutationError")
class MutationErrorType:
    code: str
    message: str
    field: str | None = None
