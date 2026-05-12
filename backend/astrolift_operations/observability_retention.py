"""
Per-org observability data retention (#160, spec 08 §12).

Resolves the effective retention period for each observability
stream (logs / metrics raw / metrics rollup / traces) given:

- the platform-wide default (settings),
- the per-org override on ``Organization`` (a positive int),
- a per-stream exception window (incident-time-range hold).

Why a separate module from ``retention.py``:

- Different streams: ``retention.py`` covers events + audit (which
  live in our DB). Observability rows live elsewhere — Loki for
  logs, Prometheus/Mimir for metrics, Tempo/Jaeger for traces —
  so the eviction call is a *driver* call, not a Django ORM
  ``DELETE``. Keeping the policy logic separate makes the seam
  obvious.
- Different defaults & override surfaces: observability retention
  has billing implications (longer = more storage cost), so the
  resolver exposes a ``billable_window_days`` view that the usage
  dashboard reads directly.

Hold windows: an org admin can declare an incident-time range
("keep logs for App=X between 2026-05-01..2026-05-03 indefinitely")
that suppresses eviction for matching rows. We model the hold as
a list of ``RetentionHold`` records the caller passes in; this
module just answers "for this row, is any hold active?".
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal

Stream = Literal["log", "metric_raw", "metric_rollup", "trace"]
ALL_STREAMS: tuple[Stream, ...] = ("log", "metric_raw", "metric_rollup", "trace")


PLATFORM_DEFAULTS: dict[Stream, int] = {
    "log": 30,
    "metric_raw": 90,
    "metric_rollup": 365,
    "trace": 14,
}


@dataclasses.dataclass(frozen=True, slots=True)
class RetentionHold:
    """An admin-placed hold that suppresses eviction.

    ``stream`` is one of the observability streams or ``"*"`` for
    "all streams". ``resource_kind``/``resource_id`` scope the hold
    (e.g. ``("App", "42")``); empty strings mean "any". The window
    is inclusive on both ends — an alert fired at the boundary stays
    held.
    """

    stream: Stream | Literal["*"]
    starts_at: datetime
    ends_at: datetime
    resource_kind: str = ""
    resource_id: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("hold timestamps must be timezone-aware")
        if self.ends_at < self.starts_at:
            raise ValueError("ends_at must be on or after starts_at")


@dataclasses.dataclass(frozen=True, slots=True)
class EffectiveRetention:
    """The resolved policy for one (org, stream) pair."""

    stream: Stream
    days: int
    source: str  # 'org_override' | 'platform_default'

    @property
    def cutoff_seconds(self) -> int:
        return self.days * 86400


def effective_for(
    *,
    stream: Stream,
    org_override_days: int | None,
) -> EffectiveRetention:
    """Resolve the effective retention for one stream.

    ``None`` org override → platform default. Negative / zero values
    on the override fall back to the default (a typo in the admin UI
    shouldn't accidentally evict everything).
    """
    if stream not in PLATFORM_DEFAULTS:
        raise ValueError(f"unknown observability stream {stream!r}")

    if org_override_days is not None and org_override_days > 0:
        return EffectiveRetention(stream=stream, days=org_override_days, source="org_override")
    return EffectiveRetention(
        stream=stream,
        days=PLATFORM_DEFAULTS[stream],
        source="platform_default",
    )


def cutoff_at(retention: EffectiveRetention, *, now: datetime) -> datetime:
    """Rows with ``timestamp < cutoff`` are eligible for eviction
    (subject to active holds — see :func:`is_held`)."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now - timedelta(days=retention.days)


def is_held(
    *,
    stream: Stream,
    timestamp: datetime,
    resource_kind: str,
    resource_id: str,
    holds: Sequence[RetentionHold],
) -> bool:
    """Returns True if any hold covers this row.

    Match rules: hold's stream is the row's stream or ``"*"``;
    hold's resource_kind/id is empty (matches any) or exact match;
    timestamp is within the hold's [starts_at, ends_at] window.
    """
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    for h in holds:
        if h.stream not in (stream, "*"):
            continue
        if h.resource_kind and h.resource_kind != resource_kind:
            continue
        if h.resource_id and h.resource_id != resource_id:
            continue
        if h.starts_at <= timestamp <= h.ends_at:
            return True
    return False


def billable_window_days(retention: EffectiveRetention) -> int:
    """Number of days the platform expects to retain. The usage
    dashboard multiplies this by the stream's $/GB/day cost to show
    the line item — the actual storage call happens in driver land,
    but billing decisions sit in the platform."""
    return retention.days


def warn_threshold_for(retention: EffectiveRetention) -> int:
    """When to warn an org admin before retention closes on a row.

    7 days for short-retention streams (≤30 days), 14 days for longer
    ones. Proportional cadence — a 14-day notice on a 14-day retention
    would fire on day 0 and never make sense.
    """
    return 7 if retention.days <= 30 else 14
