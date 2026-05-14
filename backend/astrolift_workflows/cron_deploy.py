"""
Cron-triggered deploy policy (#296, spec 06 §5).

Pure-Python policy used by the platform's cron-dispatch tick to
decide which ``RegisteredApp`` rows should fire a deploy this minute.

The dispatcher fires every minute via the schedule registry. On
each tick it:

1. Loads every active ``RegisteredApp`` with
   ``trigger_mode == 'cron' AND cron_expression != ''``.
2. Filters out rows that are soft-deleted or have ``cron_paused``
   set.
3. Matches each row's expression against the current minute via
   :func:`cron_matches`.
4. For every match, enqueues a ``StartDeployment`` (via the same
   ``start_workflow`` path the GraphQL mutation uses).

Matching is intentionally minute-granular — we don't aim to cover
the sub-minute fan-out cases. If two ticks land in the same minute
(e.g. backfill on a Temporal restart) the deploy mutation's
single-flight workflow id idempotency rejects the duplicate.

Matching the five-field cron grammar:
    minute hour day-of-month month day-of-week
where each field accepts ``*``, an integer, ``*/N``, ``A-B``, or a
comma-list of the above. Same shape :mod:`astrolift_registry.cron`
already validates at registration time.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime

from astrolift_registry.cron import CronValidationError, validate_cron_expression

# Mirror the bounds in astrolift_registry.cron (which validates the
# *shape*; this module evaluates the *value*).
_FIELD_BOUNDS: tuple[tuple[int, int], ...] = (
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day-of-month
    (1, 12),  # month
    (0, 6),  # day-of-week (0=Sun .. 6=Sat)
)

_INT_RE = re.compile(r"^\d+$")
_STEP_RE = re.compile(r"^\*/(\d+)$")
_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")


def _atom_matches(atom: str, value: int, lo: int, hi: int) -> bool:
    if atom == "*":
        return True
    if _INT_RE.match(atom):
        return int(atom) == value
    m = _STEP_RE.match(atom)
    if m is not None:
        step = int(m.group(1))
        # ``*/N`` ≡ {lo, lo+N, lo+2N, ...} within [lo, hi].
        return (value - lo) % step == 0
    m = _RANGE_RE.match(atom)
    if m is not None:
        start, end = int(m.group(1)), int(m.group(2))
        return start <= value <= end
    # Anything else means the expression was rejected by the validator
    # before it ever landed in the DB — defensively refuse to match.
    return False


def _field_matches(field: str, value: int, lo: int, hi: int) -> bool:
    for atom in field.split(","):
        atom = atom.strip()
        if _atom_matches(atom, value, lo, hi):
            return True
    return False


def cron_matches(expression: str, *, now: datetime) -> bool:
    """Does ``expression`` fire at ``now``?

    Accepts the same 5-field grammar as :func:`validate_cron_expression`.
    Naive datetimes are rejected — callers must pass tz-aware ``now``
    so the meaning is unambiguous (UTC is the platform convention).
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    try:
        normalized = validate_cron_expression(expression)
    except CronValidationError:
        return False

    fields = normalized.split()
    values = (
        now.minute,
        now.hour,
        now.day,
        now.month,
        # Python's weekday() is Monday=0..Sunday=6; cron weekday is
        # Sunday=0..Saturday=6. Convert.
        (now.weekday() + 1) % 7,
    )
    for field, value, (lo, hi) in zip(fields, values, _FIELD_BOUNDS, strict=True):
        if not _field_matches(field, value, lo, hi):
            return False
    return True


@dataclasses.dataclass(frozen=True, slots=True)
class CronDispatchCandidate:
    """One ``RegisteredApp`` row the dispatcher should consider this tick."""

    app_id: int
    app_slug: str
    app_guid: str
    cron_expression: str
    cron_paused: bool
    primary_environment_name: str | None


def select_matches(
    *,
    candidates: list[CronDispatchCandidate],
    now: datetime,
) -> list[CronDispatchCandidate]:
    """Filter ``candidates`` to those whose cron fires at ``now``.

    Skips:
      * rows with ``cron_paused=True``
      * rows whose ``primary_environment_name`` is None (no env to
        deploy to — surface upstream as an alert separately)
      * rows whose cron expression doesn't match the current minute
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    out: list[CronDispatchCandidate] = []
    for c in candidates:
        if c.cron_paused:
            continue
        if not c.primary_environment_name:
            continue
        if not cron_matches(c.cron_expression, now=now):
            continue
        out.append(c)
    return out
