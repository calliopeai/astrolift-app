"""Tiny five-field cron expression validator.

Used by ``register_app`` / ``update_app`` to reject malformed cron
expressions before they reach the model. Intentionally home-grown —
we don't want a transitive dep just to validate the shape, and we
don't want to accept seconds-or-year extensions that downstream
schedulers might not honor.

Per #290: 5 fields, whitespace-separated, each one of:

* ``*``                  — wildcard
* an integer             — within the field's valid range
* ``*/N``                — step (N a positive integer)
* ``A-B``                — closed range (both endpoints in field range,
                           A <= B)
* ``A,B,C`` (comma list) — each element is itself ``*`` / int / step /
                           range

We do NOT support named months/weekdays (``JAN``, ``MON``) or the
``L`` / ``W`` / ``#`` extensions. Those are out of scope for the
v1 wizard input.
"""

from __future__ import annotations

import re

# (min_inclusive, max_inclusive) per field, in order.
_FIELD_BOUNDS: tuple[tuple[int, int], ...] = (
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day-of-month
    (1, 12),  # month
    (0, 6),  # day-of-week (0=Sun .. 6=Sat)
)

_FIELD_NAMES: tuple[str, ...] = (
    "minute",
    "hour",
    "day-of-month",
    "month",
    "day-of-week",
)

_INT_RE = re.compile(r"^\d+$")
_STEP_RE = re.compile(r"^\*/(\d+)$")
_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")


class CronValidationError(ValueError):
    """Raised when a cron expression doesn't pass the 5-field check."""


def _validate_atom(atom: str, lo: int, hi: int, field_name: str) -> None:
    """Validate a single non-comma segment of a field."""
    if atom == "*":
        return
    if _INT_RE.match(atom):
        value = int(atom)
        if value < lo or value > hi:
            raise CronValidationError(f"{field_name} value {value} outside range {lo}-{hi}")
        return
    step_match = _STEP_RE.match(atom)
    if step_match is not None:
        step = int(step_match.group(1))
        if step <= 0:
            raise CronValidationError(f"{field_name} step must be positive, got {step}")
        # Bound the step to the field width to refuse "*/9999" nonsense.
        if step > hi - lo + 1:
            raise CronValidationError(f"{field_name} step {step} exceeds field width {hi - lo + 1}")
        return
    range_match = _RANGE_RE.match(atom)
    if range_match is not None:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        if start < lo or end > hi:
            raise CronValidationError(f"{field_name} range {start}-{end} outside {lo}-{hi}")
        if start > end:
            raise CronValidationError(f"{field_name} range {start}-{end} has start > end")
        return
    raise CronValidationError(
        f"{field_name} segment {atom!r} not understood (expected *, integer, */N, or A-B)"
    )


def _validate_field(field: str, lo: int, hi: int, field_name: str) -> None:
    if not field:
        raise CronValidationError(f"{field_name} field is empty")
    for atom in field.split(","):
        atom = atom.strip()
        if not atom:
            raise CronValidationError(f"{field_name} has empty list element")
        _validate_atom(atom, lo, hi, field_name)


def validate_cron_expression(expr: str) -> str:
    """Validate ``expr`` as a 5-field cron expression. Returns the
    normalized (single-space-separated) form on success; raises
    ``CronValidationError`` on failure.

    Empty / whitespace-only input is rejected — callers should
    pre-check and decide whether 'no cron' is legal in their context.
    """
    if expr is None or not expr.strip():
        raise CronValidationError("cron expression is empty")
    fields = expr.split()
    if len(fields) != 5:
        raise CronValidationError(f"cron expression must have exactly 5 fields, got {len(fields)}")
    for field, (lo, hi), name in zip(fields, _FIELD_BOUNDS, _FIELD_NAMES, strict=True):
        _validate_field(field, lo, hi, name)
    return " ".join(fields)
