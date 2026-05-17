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


def failure(
    code: str,
    message: str,
    *,
    field: str | None = None,
    requires_attestation: bool | None = None,
    current_version: int | None = None,
    requested_version: int | None = None,
) -> MutationResultType[None]:
    return MutationResultType(
        ok=False,
        data=None,
        errors=[
            MutationErrorType(
                code=code,
                message=message,
                field=field,
                requires_attestation=requires_attestation,
                current_version=current_version,
                requested_version=requested_version,
            )
        ],
    )


def version_mismatch(
    *, current_version: int, requested_version: int, kind: str | None = None
) -> MutationResultType[None]:
    """Optimistic-concurrency rejection envelope (#497).

    Returned when an update mutation supplied ``ifMatchVersion`` and
    the persisted row's version no longer matches. The envelope is the
    standard ``MutationResult.failure`` shape with ``code`` set to
    ``VERSION_MISMATCH`` and the current persisted version attached so
    the client can render "this changed since you opened it" copy and
    refetch the latest state.
    """
    label = kind or "Entity"
    return failure(
        "VERSION_MISMATCH",
        f"{label} was modified by another session. Refresh and retry.",
        current_version=current_version,
        requested_version=requested_version,
    )
