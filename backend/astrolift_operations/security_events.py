"""
Security event detection + alert pipeline policy (#151, spec 12 §13).

Three detectors, all framed as the same shape: a sliding-window
counter over a (subject, kind) key with a threshold. The detectors
are pure — callers feed in event timestamps and get back severity
plus de-duplication-aware alert decisions.

Why pure:

- Counts can come from anywhere — Redis, the events table, a
  Prometheus query — and we don't want the policy code coupled
  to one storage choice.
- Detection thresholds are config-driven (per-org overrides).
  Pure functions over a config dataclass make that trivial.

The three detectors:

  - failed_login_burst:      N failed logins / user / window
  - token_from_new_ip:       API token used from an unseen IP
  - permission_denied_burst: 403s from one actor in a window

De-duplication: when an alert fires for a (kind, subject) pair, we
remember the time. Subsequent triggers within ``cooldown_seconds``
don't re-fire — the on-call SREs should see one PagerDuty per
incident, not 50.

Severity escalation: bursts past 5x the threshold escalate from
WARNING to CRITICAL. The auto-remediation hook (e.g. lock account)
fires when severity reaches CRITICAL.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from datetime import datetime, timedelta
from enum import Enum


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class DetectorKind(str, Enum):
    FAILED_LOGIN_BURST = "failed_login_burst"
    TOKEN_FROM_NEW_IP = "token_from_new_ip"
    PERMISSION_DENIED_BURST = "permission_denied_burst"


# Defaults match spec 12 §13. Overridable per-org via settings or
# Organization fields (caller hands the resolved config in).
DEFAULTS = {
    DetectorKind.FAILED_LOGIN_BURST: {
        "threshold": 5,
        "window_seconds": 5 * 60,
        "cooldown_seconds": 30 * 60,
        "auto_remediate_at": 10,  # lock the account at 10 failures
    },
    DetectorKind.TOKEN_FROM_NEW_IP: {
        "threshold": 1,           # any unseen-IP use trips it once
        "window_seconds": 0,      # not windowed; first-use detection
        "cooldown_seconds": 24 * 3600,
        "auto_remediate_at": None,  # not auto-remediated; informational
    },
    DetectorKind.PERMISSION_DENIED_BURST: {
        "threshold": 10,
        "window_seconds": 60,
        "cooldown_seconds": 5 * 60,
        "auto_remediate_at": 50,  # rate-limit the actor at 50 denials
    },
}


@dataclasses.dataclass(frozen=True, slots=True)
class DetectorConfig:
    """The thresholds the policy evaluates against. Per-org overrides
    flow into this dataclass — the resolver code is unaware of orgs."""

    kind: DetectorKind
    threshold: int
    window_seconds: int
    cooldown_seconds: int
    auto_remediate_at: int | None = None

    def __post_init__(self) -> None:
        if self.threshold <= 0:
            raise ValueError("threshold must be positive")
        if self.window_seconds < 0:
            raise ValueError("window_seconds must be non-negative")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be non-negative")


def config_with_defaults(
    kind: DetectorKind, *, overrides: dict | None = None
) -> DetectorConfig:
    """Merge per-org overrides with defaults."""
    base = dict(DEFAULTS[kind])
    if overrides:
        base.update({k: v for k, v in overrides.items() if k in base})
    return DetectorConfig(kind=kind, **base)


@dataclasses.dataclass(frozen=True, slots=True)
class AlertDecision:
    """The output of evaluating one window of events."""

    fire: bool
    suppressed_by_cooldown: bool
    severity: Severity
    count: int
    auto_remediate: bool
    reason: str


def evaluate(
    *,
    config: DetectorConfig,
    event_count_in_window: int,
    last_alert_at: datetime | None,
    now: datetime,
) -> AlertDecision:
    """Decide whether to fire an alert for this (subject, kind).

    The caller assembles ``event_count_in_window`` from whatever
    counter store fits — Redis, an events query, etc. — and passes
    in the most recent alert timestamp (if any) for cooldown.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    if event_count_in_window < config.threshold:
        return AlertDecision(
            fire=False,
            suppressed_by_cooldown=False,
            severity=Severity.INFO,
            count=event_count_in_window,
            auto_remediate=False,
            reason="below threshold",
        )

    severity = (
        Severity.CRITICAL
        if event_count_in_window >= config.threshold * 5
        else Severity.WARNING
    )

    auto_remediate = (
        config.auto_remediate_at is not None
        and event_count_in_window >= config.auto_remediate_at
    )

    suppressed = False
    if last_alert_at is not None:
        if last_alert_at.tzinfo is None:
            raise ValueError("last_alert_at must be timezone-aware")
        elapsed = (now - last_alert_at).total_seconds()
        # Cooldown DOES NOT suppress auto-remediation: even within
        # cooldown, if the count crosses auto_remediate_at we still
        # want the lock-account action to fire (the SRE alert was
        # the awareness; the lock is the response).
        if elapsed < config.cooldown_seconds and not auto_remediate:
            suppressed = True

    fire = not suppressed
    return AlertDecision(
        fire=fire,
        suppressed_by_cooldown=suppressed,
        severity=severity,
        count=event_count_in_window,
        auto_remediate=auto_remediate,
        reason=(
            "cooldown" if suppressed
            else f"threshold crossed ({event_count_in_window} >= {config.threshold})"
        ),
    )


def count_within_window(
    *, event_times: Iterable[datetime], window_seconds: int, now: datetime
) -> int:
    """Helper for callers that have a list of event times — return
    how many fall inside ``[now - window, now]``. Useful when the
    caller is reading from the audit log directly rather than a
    counter store."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if window_seconds < 0:
        raise ValueError("window_seconds must be non-negative")
    cutoff = now - timedelta(seconds=window_seconds)
    n = 0
    for ts in event_times:
        if ts.tzinfo is None:
            raise ValueError("event_times must be timezone-aware")
        if cutoff <= ts <= now:
            n += 1
    return n
