"""
Generic ``MutationResult`` Strawberry binding.

Strawberry generates the concrete type per ``data`` payload at schema
build time (see ``MutationResultType[Organization]`` etc. in the
per-app schema modules).
"""

from __future__ import annotations

from typing import TypeVar

import strawberry

from astrolift_graphql.errors import MutationErrorType

T = TypeVar("T")


@strawberry.type(name="MutationResult")
class MutationResultType[T]:
    ok: bool
    errors: list[MutationErrorType] = strawberry.field(default_factory=list)
    data: T | None = None


def success[T](data: T) -> MutationResultType[T]:
    return MutationResultType(ok=True, data=data, errors=[])


def failure(code: str, message: str, *, field: str | None = None) -> MutationResultType[None]:
    return MutationResultType(
        ok=False,
        data=None,
        errors=[MutationErrorType(code=code, message=message, field=field)],
    )
