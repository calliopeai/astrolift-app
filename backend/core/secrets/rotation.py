"""
Rotation policy logic (#150, spec 12 §6.3).

Pure-Python policy engine: given a secret's last-rotated timestamp,
its rotation period, and the current time, decide:

- whether the secret is **due** for rotation now,
- when it is **next** due,
- which **reminder thresholds** (e.g. 14d / 7d / 1d before expiry) have
  been crossed since the last check (so the notification system can
  send "rotates in N days" warnings exactly once per crossing).

Why this lives separate from the workflow that mints new credentials:

* Mint logic is provider-specific (RDS, CloudSQL, Vault, …) and
  belongs in driver impls. The policy is universal.
* Tests around policy don't need a worker / Temporal / cloud SDKs.
* The same policy answers UI questions ("show 'rotates in 6 days'
  badge") and worker questions ("kick off rotation now").

Defaults match spec 12 §6.3: 90-day default rotation; reminders at
14, 7, and 1 days before expiry. Both are overridable per-secret.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

# Spec 12 §6.3 defaults — overridable per RotationPolicy row.
DEFAULT_ROTATION_PERIOD_DAYS = 90
DEFAULT_REMINDER_THRESHOLDS_DAYS: tuple[int, ...] = (14, 7, 1)


@dataclasses.dataclass(frozen=True, slots=True)
class RotationPolicy:
    """The state needed to make rotation decisions for one secret.

    Callers map this to whatever model row holds the secret
    (ManagedServiceCredential, DeployToken, ApiToken, TlsCertificate
    via not_after — see ``next_due`` notes).

    ``last_rotated_at`` MUST be timezone-aware. Naive datetimes are
    rejected at construct time so the comparison logic doesn't have
    to handle a mix.
    """

    last_rotated_at: datetime
    period_days: int = DEFAULT_ROTATION_PERIOD_DAYS
    reminder_thresholds_days: tuple[int, ...] = DEFAULT_REMINDER_THRESHOLDS_DAYS

    def __post_init__(self) -> None:
        if self.last_rotated_at.tzinfo is None:
            raise ValueError("last_rotated_at must be timezone-aware")
        if self.period_days <= 0:
            raise ValueError("period_days must be positive")
        # Sorted desc so the largest threshold (e.g. 14d) fires first
        # when crossings are computed in chronological order.
        for t in self.reminder_thresholds_days:
            if t <= 0:
                raise ValueError(f"reminder threshold {t} must be positive")


@dataclasses.dataclass(frozen=True, slots=True)
class RotationStatus:
    """The output of ``evaluate(policy, now)``."""

    is_due: bool
    next_due_at: datetime
    days_until_due: int  # negative when overdue
    reminders_to_fire: tuple[int, ...]  # threshold days that just crossed


def next_due_at(policy: RotationPolicy) -> datetime:
    """Pure: when does this secret next need rotation?"""
    return policy.last_rotated_at + timedelta(days=policy.period_days)


def is_due(policy: RotationPolicy, *, now: datetime) -> bool:
    """Pure: is the secret past its rotation deadline at ``now``?"""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now >= next_due_at(policy)


def reminders_to_fire(
    policy: RotationPolicy,
    *,
    now: datetime,
    last_check_at: datetime | None,
) -> tuple[int, ...]:
    """Threshold days whose **crossing** lies between ``last_check_at``
    and ``now``. Returned in descending order (14d before 7d before 1d).

    Why crossings, not 'currently within window':
      A naive 'days_remaining <= threshold' check fires the same
      reminder every time the worker polls — not once. We only want
      to notify the *first* time we step inside a threshold. If
      ``last_check_at`` is None this is the first evaluation, so any
      threshold that's currently active fires.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if last_check_at is not None and last_check_at.tzinfo is None:
        raise ValueError("last_check_at must be timezone-aware when provided")

    due = next_due_at(policy)
    crossings: list[int] = []
    for threshold in sorted(policy.reminder_thresholds_days, reverse=True):
        boundary = due - timedelta(days=threshold)
        # We've stepped past `boundary` if now is at-or-after it.
        # We want to fire only when we *just* crossed — i.e. the
        # last check was before the boundary. ``last_check_at=None``
        # treats the first evaluation as "everything just crossed".
        crossed_now = now >= boundary
        was_already_crossed = (
            last_check_at is not None and last_check_at >= boundary
        )
        if crossed_now and not was_already_crossed:
            crossings.append(threshold)
    return tuple(crossings)


def evaluate(
    policy: RotationPolicy,
    *,
    now: datetime,
    last_check_at: datetime | None = None,
) -> RotationStatus:
    """One-shot evaluation: due flag, next due time, days remaining,
    and any reminder crossings.

    Workers call this on a poll loop (e.g. every hour); the UI calls
    it with ``last_check_at=None`` to compute a current state badge.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    due_at = next_due_at(policy)
    delta = due_at - now
    # Round toward zero (truthful days remaining; -1 means 'one day
    # overdue', 0 means 'due today or already overdue within 24h').
    days_until = int(delta.total_seconds() // 86400)

    return RotationStatus(
        is_due=now >= due_at,
        next_due_at=due_at,
        days_until_due=days_until,
        reminders_to_fire=reminders_to_fire(
            policy, now=now, last_check_at=last_check_at
        ),
    )


def utcnow() -> datetime:
    """Tiny helper so callers don't have to remember the tzinfo dance.
    Tests pass a fixed datetime instead — never call this from tests."""
    return datetime.now(timezone.utc)
