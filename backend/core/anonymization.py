"""
PII anonymization for GDPR right-to-delete (#169 part 2,
spec 04 §11).

Anonymizes a user record + scrubs PII from event payloads while
preserving referential integrity. The user row stays (so audit
log entries that point at it still resolve), but every field
that could identify the human is replaced with a deterministic
placeholder.

Why preserve the row:

- Audit log entries may FK to the user. Deleting the row would
  cascade-blast the audit trail — the opposite of what compliance
  requires.
- 'User 42 did X on date Y' as audit data is fine; 'Alice did X
  on date Y' is the PII risk. Keep ID + timestamps, scrub the
  identity.

Deterministic placeholders use the user PK so re-running the
anonymizer produces the same output (idempotent + reproducible
in tests).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from datetime import datetime, timedelta


# Spec 04 §11 default — 30-day grace period before anonymization
# kicks in after a deletion request. Lets the user reverse course.
DEFAULT_GRACE_DAYS = 30


# Field set: known PII columns on the platform's User model + adjacent
# rows. Adding a new PII column to the user table also requires
# adding it here. The audit-log scrubber works off a separate map
# (event payload keys to scrub) since payloads are JSON.
USER_PII_FIELDS: tuple[str, ...] = (
    "email",
    "first_name",
    "last_name",
    "username",
    "phone",
    "avatar_url",
)

# Event/audit payload keys that may carry PII. Scrubbed in place.
EVENT_PAYLOAD_PII_KEYS: tuple[str, ...] = (
    "ip_address",
    "user_agent",
    "email",
    "request_email",
    "actor_email",
)


@dataclasses.dataclass(frozen=True, slots=True)
class AnonymizedFields:
    """The values to write to the user row. Caller does the actual
    ORM ``.save()`` so this module stays Django-free for tests."""

    placeholders: dict[str, str]
    is_active: bool = False  # accounts deactivated as part of anonymization


def deterministic_placeholders(
    user_id: int, *, fields: Iterable[str] = USER_PII_FIELDS
) -> AnonymizedFields:
    """Compute placeholders for ``user_id``.

    Format: ``anon-<user_id>@deleted.local`` for email-shaped fields,
    ``deleted-user-<user_id>`` otherwise. Choosing recognisable
    placeholders means an operator browsing the DB sees
    'this is anonymized' rather than wondering if it's real data."""
    out: dict[str, str] = {}
    for field in fields:
        if "email" in field:
            out[field] = f"anon-{user_id}@deleted.local"
        elif field == "avatar_url":
            out[field] = ""
        else:
            out[field] = f"deleted-user-{user_id}"
    return AnonymizedFields(placeholders=out)


def scrub_event_payload(
    payload: dict, *, keys: Iterable[str] = EVENT_PAYLOAD_PII_KEYS
) -> dict:
    """Return a new payload with PII keys replaced by ``"[anonymized]"``.

    Walks one level deep; nested dicts are not processed. Event
    payloads in the platform are flat by convention (spec 17 §2)
    so this matches reality. Tests that pass nested structures will
    see only top-level keys scrubbed — by design.
    """
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    for key in keys:
        if key in out:
            out[key] = "[anonymized]"
    return out


# ---- grace period ----------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class AnonymizationSchedule:
    """When does this user become eligible for anonymization?"""

    requested_at: datetime
    eligible_at: datetime

    def is_due(self, *, now: datetime) -> bool:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return now >= self.eligible_at


def schedule_for_deletion(
    *,
    requested_at: datetime,
    grace_days: int = DEFAULT_GRACE_DAYS,
) -> AnonymizationSchedule:
    """Compute the eligibility timestamp. Caller persists this onto
    a ``DeletionRequest`` row; the scheduled task scans for due rows
    and runs anonymization."""
    if requested_at.tzinfo is None:
        raise ValueError("requested_at must be timezone-aware")
    if grace_days < 0:
        raise ValueError("grace_days must be non-negative")
    return AnonymizationSchedule(
        requested_at=requested_at,
        eligible_at=requested_at + timedelta(days=grace_days),
    )
