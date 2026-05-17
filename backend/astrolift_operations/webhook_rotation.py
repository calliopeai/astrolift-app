"""
Webhook secret rotation policy (#426).

Rotation moves the current ``secret_hash`` into
``secret_hash_previous`` and writes a fresh plaintext secret + hash.
The previous hash stays valid for the Constance grace window so
subscribers can roll out the new secret without dropping deliveries.

The grace window is consulted by ``previous_secret_is_in_window`` —
verifiers that have access to the subscription's ``secret_rotated_at``
check whether ``previous_secret_hash`` should still be honoured.

This module is pure-policy (no DB writes) so tests don't need a
DB. The mutation layer is the only writer.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import secrets

DEFAULT_GRACE_SECONDS = 60 * 60  # 1h; matches the Constance default
MIN_GRACE_SECONDS = 60
MAX_GRACE_SECONDS = 24 * 60 * 60  # 24h ceiling

SECRET_PREFIX = "alfthk_"


class RotationError(ValueError):
    pass


@dataclasses.dataclass(frozen=True, slots=True)
class RotationPlan:
    """Output of ``plan_rotation``. The caller persists the new
    hash + previous hash + ``rotated_at`` in one save; the
    plaintext is returned to the operator exactly once."""

    plaintext_secret: str
    new_secret_hash: str
    previous_secret_hash: str
    rotated_at: dt.datetime
    grace_seconds: int


def plan_rotation(
    *,
    current_secret_hash: str,
    now: dt.datetime,
    grace_seconds: int = DEFAULT_GRACE_SECONDS,
) -> RotationPlan:
    """Build a rotation plan. The caller writes the result into the
    subscription row."""
    if grace_seconds < MIN_GRACE_SECONDS:
        raise RotationError(f"grace_seconds must be >= {MIN_GRACE_SECONDS}, got {grace_seconds}")
    if grace_seconds > MAX_GRACE_SECONDS:
        raise RotationError(f"grace_seconds must be <= {MAX_GRACE_SECONDS} (24h), got {grace_seconds}")
    plaintext = SECRET_PREFIX + secrets.token_urlsafe(24)
    new_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    return RotationPlan(
        plaintext_secret=plaintext,
        new_secret_hash=new_hash,
        previous_secret_hash=current_secret_hash or "",
        rotated_at=now,
        grace_seconds=int(grace_seconds),
    )


def previous_secret_is_in_window(
    *,
    rotated_at: dt.datetime | None,
    now: dt.datetime,
    grace_seconds: int,
) -> bool:
    """Verifier-side check: was the previous secret rotated recently
    enough that it should still be honoured? ``rotated_at`` of None
    means no prior rotation; the previous secret never gets used."""
    if rotated_at is None:
        return False
    if grace_seconds <= 0:
        return False
    return (now - rotated_at).total_seconds() <= grace_seconds


def grace_seconds_from_constance() -> int:
    """Resolve the operator-tunable grace window. Falls back to the
    pure-policy default if Constance is unavailable (DB not migrated,
    plugin disabled, test bootstrap) so rotation never fails on a
    side-channel that's out of the rotation's control."""
    try:
        from constance import config as constance_config

        raw = getattr(constance_config, "WEBHOOK_SECRET_ROTATION_GRACE_SECONDS", DEFAULT_GRACE_SECONDS)
        value = int(raw)
    except Exception:
        return DEFAULT_GRACE_SECONDS
    if value < MIN_GRACE_SECONDS:
        return MIN_GRACE_SECONDS
    if value > MAX_GRACE_SECONDS:
        return MAX_GRACE_SECONDS
    return value
