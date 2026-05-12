"""
TLS certificate expiry monitoring (#155, spec 13 §5.3).

Daily scheduled check evaluates each active certificate's expiry
window and emits notifications at fixed thresholds (30 / 14 / 7 days).
A 7-day breach without successful renewal escalates to the platform
operator.

The renewal *trigger* (calling cert-manager / TlsDriver.issue) lives
in the workflow — this module is the policy layer that decides
when to alert, when to escalate, and what state every cert is in.
Same shape as ``rotation.py``: pure functions over a small dataclass,
crossings rather than 'currently within window' so each threshold
fires exactly once.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from enum import Enum

# Spec 13 §5.3: 30 / 14 / 7 day reminders, 7-day escalation.
EXPIRY_THRESHOLDS_DAYS: tuple[int, ...] = (30, 14, 7)
ESCALATION_THRESHOLD_DAYS: int = 7


class CertHealth(str, Enum):
    """Bucket each certificate into one of these states for the
    cert-health dashboard. Not all states map 1:1 to threshold
    crossings — health is a snapshot, crossings are events."""

    HEALTHY = "healthy"
    WARNING = "warning"  # within 30d
    URGENT = "urgent"  # within 14d
    CRITICAL = "critical"  # within 7d (escalation territory)
    EXPIRED = "expired"
    RENEWAL_FAILED = "renewal_failed"


@dataclasses.dataclass(frozen=True, slots=True)
class CertSnapshot:
    """Inputs to the policy. Caller maps from the platform's
    TlsCertificate row (or the cluster-driver's CertificateHandle)."""

    not_after: datetime  # the cert's expiry
    last_renewal_failed: bool = False  # most recent renewal attempt outcome
    renewal_attempts: int = 0  # consecutive failed renewals

    def __post_init__(self) -> None:
        if self.not_after.tzinfo is None:
            raise ValueError("not_after must be timezone-aware")


@dataclasses.dataclass(frozen=True, slots=True)
class CertStatus:
    """Output of one evaluation."""

    health: CertHealth
    days_until_expiry: int  # negative when already expired
    thresholds_to_fire: tuple[int, ...]  # which reminders just crossed
    should_escalate: bool  # within escalation window AND no recent success


def evaluate(
    snapshot: CertSnapshot,
    *,
    now: datetime,
    last_check_at: datetime | None = None,
) -> CertStatus:
    """Compute the cert's current state at ``now``.

    ``last_check_at`` controls the threshold-crossing logic — same
    pattern as ``rotation.py``. ``None`` makes every currently-crossed
    threshold fire (for first-pass seeds + UI snapshots that don't
    care about crossings, callers pass ``None`` and ignore the
    ``thresholds_to_fire`` field).
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if last_check_at is not None and last_check_at.tzinfo is None:
        raise ValueError("last_check_at must be timezone-aware when provided")

    delta = snapshot.not_after - now
    days_until = int(delta.total_seconds() // 86400)

    health = _bucket(snapshot, days_until)
    crossings = _crossings(not_after=snapshot.not_after, now=now, last_check_at=last_check_at)
    should_escalate = days_until <= ESCALATION_THRESHOLD_DAYS and snapshot.last_renewal_failed

    return CertStatus(
        health=health,
        days_until_expiry=days_until,
        thresholds_to_fire=crossings,
        should_escalate=should_escalate,
    )


def _bucket(snap: CertSnapshot, days_until: int) -> CertHealth:
    if snap.last_renewal_failed and snap.renewal_attempts >= 1:
        # An expired cert that also failed renewal is still 'expired'
        # in the dashboard — the more severe state wins so operators
        # see the outage, not the failed retry.
        if days_until < 0:
            return CertHealth.EXPIRED
        return CertHealth.RENEWAL_FAILED
    if days_until < 0:
        return CertHealth.EXPIRED
    if days_until <= 7:
        return CertHealth.CRITICAL
    if days_until <= 14:
        return CertHealth.URGENT
    if days_until <= 30:
        return CertHealth.WARNING
    return CertHealth.HEALTHY


def _crossings(*, not_after: datetime, now: datetime, last_check_at: datetime | None) -> tuple[int, ...]:
    fired: list[int] = []
    for threshold in sorted(EXPIRY_THRESHOLDS_DAYS, reverse=True):
        boundary = not_after - timedelta(days=threshold)
        crossed_now = now >= boundary
        was_already_crossed = last_check_at is not None and last_check_at >= boundary
        if crossed_now and not was_already_crossed:
            fired.append(threshold)
    return tuple(fired)
