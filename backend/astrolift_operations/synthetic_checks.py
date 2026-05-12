"""
Synthetic-check policy + outcome aggregation (#23, spec 08 §11).

Three probe kinds:

* **HTTP health** — GET to ``https://<host>/<healthcheck_path>``
  every 30s. Multi-region egress probes optional.
* **TLS expiry** — daily scan; emits events at 30/7-day thresholds.
* **DNS resolution** — every 5m; verifies the hostname resolves.

Pure-policy module. The actual fetch/probe logic lives in workers
(or in driver land for cloud-native synthetic services); this
module owns:

  - Default schedules + thresholds (spec defaults; per-app override).
  - The aggregation rule that turns N probe outcomes into one
    *health bucket* the UI badge displays.
  - The TLS-expiry threshold-crossing logic (reuses the pattern
    from rotation.py / cert_expiry.py).
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from enum import Enum


class CheckKind(str, Enum):
    HTTP = "http"
    TLS_EXPIRY = "tls_expiry"
    DNS = "dns"


class HealthBadge(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"  # some probes failing
    DOWN = "down"  # majority failing
    UNKNOWN = "unknown"  # no recent probes


# Spec 08 §11 defaults.
DEFAULT_INTERVALS_SECONDS: dict[CheckKind, int] = {
    CheckKind.HTTP: 30,
    CheckKind.TLS_EXPIRY: 24 * 3600,  # daily
    CheckKind.DNS: 5 * 60,
}

TLS_WARN_THRESHOLDS_DAYS: tuple[int, ...] = (30, 7)


@dataclasses.dataclass(frozen=True, slots=True)
class CheckSchedule:
    """Per-app schedule. Empty fields fall back to defaults."""

    http_interval_seconds: int | None = None
    tls_interval_seconds: int | None = None
    dns_interval_seconds: int | None = None

    def for_kind(self, kind: CheckKind) -> int:
        """Resolve the effective interval. Per-app override wins;
        else platform default."""
        override = {
            CheckKind.HTTP: self.http_interval_seconds,
            CheckKind.TLS_EXPIRY: self.tls_interval_seconds,
            CheckKind.DNS: self.dns_interval_seconds,
        }[kind]
        if override is not None and override > 0:
            return override
        return DEFAULT_INTERVALS_SECONDS[kind]


@dataclasses.dataclass(frozen=True, slots=True)
class ProbeOutcome:
    """One probe attempt's outcome. ``ok`` is True when the probe
    passed; ``ts`` carries when it ran. ``region`` is the worker's
    egress region (for multi-region HTTP probes)."""

    kind: CheckKind
    ok: bool
    ts: datetime
    region: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None:
            raise ValueError("ts must be timezone-aware")


def health_badge(
    *,
    outcomes: list[ProbeOutcome],
    now: datetime,
    stale_after_seconds: int = 5 * 60,
) -> HealthBadge:
    """Aggregate recent probe outcomes into a UI badge.

    Rules:
      - No outcomes within ``stale_after_seconds`` → UNKNOWN.
        (Better to surface 'we don't know' than show stale data.)
      - ≥ 50% recent outcomes failing → DOWN.
      - Any failures but minority → DEGRADED.
      - All recent passing → HEALTHY.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    cutoff = now - timedelta(seconds=stale_after_seconds)
    recent = [o for o in outcomes if o.ts >= cutoff]
    if not recent:
        return HealthBadge.UNKNOWN

    failures = sum(1 for o in recent if not o.ok)
    if failures == 0:
        return HealthBadge.HEALTHY
    if failures * 2 >= len(recent):
        return HealthBadge.DOWN
    return HealthBadge.DEGRADED


# ---- TLS expiry threshold logic ------------------------------------


def tls_thresholds_to_emit(
    *,
    not_after: datetime,
    now: datetime,
    last_check_at: datetime | None,
) -> tuple[int, ...]:
    """Which TLS warning thresholds (days) just crossed since
    ``last_check_at`` and ``now``. Same pattern as
    ``rotation.py`` / ``cert_expiry.py`` — fire each exactly once
    at the moment of crossing so daily TLS scans don't spam the
    same email every day.

    Always returned in descending order: 30 before 7.
    """
    if not_after.tzinfo is None:
        raise ValueError("not_after must be timezone-aware")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if last_check_at is not None and last_check_at.tzinfo is None:
        raise ValueError("last_check_at must be tz-aware when provided")

    fired: list[int] = []
    for threshold in sorted(TLS_WARN_THRESHOLDS_DAYS, reverse=True):
        boundary = not_after - timedelta(days=threshold)
        crossed_now = now >= boundary
        was_already_crossed = last_check_at is not None and last_check_at >= boundary
        if crossed_now and not was_already_crossed:
            fired.append(threshold)
    return tuple(fired)


def is_expired(*, not_after: datetime, now: datetime) -> bool:
    """Already past the cert's expiry."""
    if not_after.tzinfo is None or now.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return now >= not_after
