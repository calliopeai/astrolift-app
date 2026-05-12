"""Tests for security event detection (#151, spec 12 §13)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from astrolift_operations.security_events import (
    DEFAULTS,
    DetectorConfig,
    DetectorKind,
    Severity,
    config_with_defaults,
    count_within_window,
    evaluate,
)

UTC = UTC


def _now(*, off: int = 0) -> datetime:
    return datetime(2026, 5, 9, 12, tzinfo=UTC) + timedelta(seconds=off)


# ---- config ---------------------------------------------------------


def test_defaults_match_spec_12():
    """Spec 12 §13: 5 failed logins / 5 min, 10 denials / minute."""
    failed = DEFAULTS[DetectorKind.FAILED_LOGIN_BURST]
    assert failed["threshold"] == 5
    assert failed["window_seconds"] == 300
    assert failed["auto_remediate_at"] == 10
    denied = DEFAULTS[DetectorKind.PERMISSION_DENIED_BURST]
    assert denied["threshold"] == 10
    assert denied["window_seconds"] == 60


def test_config_with_defaults_applies_overrides():
    cfg = config_with_defaults(DetectorKind.FAILED_LOGIN_BURST, overrides={"threshold": 3})
    assert cfg.threshold == 3
    assert cfg.window_seconds == 300


def test_config_with_defaults_ignores_unknown_keys():
    """Defensive: a typo in the org config shouldn't bypass the
    defaults silently or raise from the defaults dict."""
    cfg = config_with_defaults(
        DetectorKind.FAILED_LOGIN_BURST,
        overrides={"threshold_typo": 999, "threshold": 3},
    )
    assert cfg.threshold == 3


def test_config_rejects_nonpositive_threshold():
    with pytest.raises(ValueError):
        DetectorConfig(
            kind=DetectorKind.FAILED_LOGIN_BURST,
            threshold=0,
            window_seconds=300,
            cooldown_seconds=60,
        )


# ---- evaluate -------------------------------------------------------


def _failed_cfg() -> DetectorConfig:
    return config_with_defaults(DetectorKind.FAILED_LOGIN_BURST)


def test_below_threshold_no_alert():
    out = evaluate(
        config=_failed_cfg(),
        event_count_in_window=4,
        last_alert_at=None,
        now=_now(),
    )
    assert out.fire is False
    assert out.severity == Severity.INFO


def test_at_threshold_fires_warning():
    out = evaluate(
        config=_failed_cfg(),
        event_count_in_window=5,
        last_alert_at=None,
        now=_now(),
    )
    assert out.fire is True
    assert out.severity == Severity.WARNING
    assert out.auto_remediate is False  # 5 < 10
    assert out.suppressed_by_cooldown is False


def test_critical_severity_at_5x_threshold():
    out = evaluate(
        config=_failed_cfg(),
        event_count_in_window=25,  # 5 * 5
        last_alert_at=None,
        now=_now(),
    )
    assert out.severity == Severity.CRITICAL


def test_auto_remediate_at_remediation_threshold():
    """5 failures = warn; 10 failures = critical + lock account."""
    out = evaluate(
        config=_failed_cfg(),
        event_count_in_window=10,
        last_alert_at=None,
        now=_now(),
    )
    assert out.auto_remediate is True


def test_cooldown_suppresses_repeat_alerts():
    cfg = _failed_cfg()  # 30 min cooldown
    out = evaluate(
        config=cfg,
        event_count_in_window=5,
        last_alert_at=_now(off=-60),  # 1 min ago
        now=_now(),
    )
    assert out.fire is False
    assert out.suppressed_by_cooldown is True


def test_cooldown_does_not_suppress_auto_remediation():
    """Critical UX: even within cooldown, if the count escalates to
    auto-remediate territory, lock the account. Cooldown is for the
    SRE alert; remediation is the response."""
    cfg = _failed_cfg()
    out = evaluate(
        config=cfg,
        event_count_in_window=15,  # past auto_remediate_at=10
        last_alert_at=_now(off=-60),
        now=_now(),
    )
    assert out.fire is True  # NOT suppressed
    assert out.auto_remediate is True


def test_cooldown_lifted_after_window():
    cfg = _failed_cfg()  # 30 min cooldown
    out = evaluate(
        config=cfg,
        event_count_in_window=5,
        last_alert_at=_now(off=-3600),  # 1 hour ago, well past 30 min
        now=_now(),
    )
    assert out.fire is True


# ---- count_within_window helper -------------------------------------


def test_count_within_window_counts_inclusive():
    times = [_now(off=-30), _now(off=-90), _now(off=-200)]
    n = count_within_window(event_times=times, window_seconds=120, now=_now())
    # -30s and -90s fit; -200s doesn't
    assert n == 2


def test_count_within_window_zero_window_only_now():
    """zero window = a strict 'right at this moment' counter, used
    by token_from_new_ip which tracks first-occurrence not bursts."""
    times = [_now(), _now(off=-1)]
    n = count_within_window(event_times=times, window_seconds=0, now=_now())
    # Only the exactly-now event counts (boundary inclusive)
    assert n == 1


def test_count_window_rejects_naive():
    with pytest.raises(ValueError):
        count_within_window(
            event_times=[datetime(2026, 5, 9)],
            window_seconds=60,
            now=_now(),
        )
    with pytest.raises(ValueError):
        count_within_window(
            event_times=[],
            window_seconds=-1,
            now=_now(),
        )
    with pytest.raises(ValueError):
        count_within_window(
            event_times=[],
            window_seconds=60,
            now=datetime(2026, 5, 9),
        )
