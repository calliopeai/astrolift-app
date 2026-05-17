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
    # #496 — set when a STEP_UP_REQUIRED envelope was raised because
    # the session is mobile + the install requires device attestation
    # + the session isn't attested. FE branches on this to open the
    # attest-prompt instead of the password-prompt. Null on every
    # other envelope.
    requires_attestation: bool | None = None
    # Optimistic-concurrency context (#497). Populated only when
    # ``code == VERSION_MISMATCH`` so the client can show
    # "this entity changed since you opened it; current is N" copy
    # and refetch the latest server-side version. Null on every
    # other error code.
    current_version: int | None = None
    requested_version: int | None = None
