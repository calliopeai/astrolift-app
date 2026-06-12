"""Unit tests for the cluster heartbeat liveness policy (#808).

Pure-Python policy — no DB. Pins the band boundaries that drive the
live-status badge so a future cadence tweak can't silently shift
'connected' into 'offline' (or vice-versa) without a failing test.
"""

from __future__ import annotations

import datetime as dt

from astrolift_clusters.heartbeat_status import (
    CONNECTED_GRACE_FACTOR,
    OFFLINE_MISS_THRESHOLD,
    HeartbeatStatus,
    heartbeat_age_seconds,
    is_live,
    resolve,
)

_NOW = dt.datetime(2026, 6, 11, 12, 0, 0, tzinfo=dt.UTC)


def _ago(seconds: float) -> dt.datetime:
    return _NOW - dt.timedelta(seconds=seconds)


# ---- never seen ---------------------------------------------------


def test_never_seen_when_no_heartbeat():
    assert resolve(last_heartbeat_at=None, interval_seconds=30, now=_NOW) is HeartbeatStatus.NEVER_SEEN


def test_age_is_none_when_never_seen():
    assert heartbeat_age_seconds(last_heartbeat_at=None, now=_NOW) is None


# ---- connected band -----------------------------------------------


def test_connected_when_pulse_just_landed():
    assert resolve(last_heartbeat_at=_ago(1), interval_seconds=30, now=_NOW) is HeartbeatStatus.CONNECTED


def test_connected_at_exactly_one_interval():
    # 30s old with a 30s interval — comfortably inside the grace band.
    assert resolve(last_heartbeat_at=_ago(30), interval_seconds=30, now=_NOW) is HeartbeatStatus.CONNECTED


def test_connected_at_grace_boundary_inclusive():
    # Exactly interval * grace is still connected (boundary is <=).
    age = 30 * CONNECTED_GRACE_FACTOR
    assert resolve(last_heartbeat_at=_ago(age), interval_seconds=30, now=_NOW) is HeartbeatStatus.CONNECTED


# ---- degraded band ------------------------------------------------


def test_degraded_just_past_grace_boundary():
    age = 30 * CONNECTED_GRACE_FACTOR + 0.1
    assert resolve(last_heartbeat_at=_ago(age), interval_seconds=30, now=_NOW) is HeartbeatStatus.DEGRADED


def test_degraded_just_below_offline_threshold():
    # One tick under the offline threshold stays degraded.
    age = 30 * OFFLINE_MISS_THRESHOLD - 0.1
    assert resolve(last_heartbeat_at=_ago(age), interval_seconds=30, now=_NOW) is HeartbeatStatus.DEGRADED


# ---- offline band -------------------------------------------------


def test_offline_at_threshold_inclusive():
    # Exactly interval * threshold flips to offline (boundary is >=).
    age = 30 * OFFLINE_MISS_THRESHOLD
    assert resolve(last_heartbeat_at=_ago(age), interval_seconds=30, now=_NOW) is HeartbeatStatus.OFFLINE


def test_offline_when_long_silent():
    assert resolve(last_heartbeat_at=_ago(3600), interval_seconds=30, now=_NOW) is HeartbeatStatus.OFFLINE


# ---- interval flooring --------------------------------------------


def test_zero_interval_floored_not_all_offline():
    # A misconfigured 0s interval must not collapse a fresh pulse to
    # OFFLINE — the policy floors the interval to MIN_INTERVAL_SECONDS.
    assert resolve(last_heartbeat_at=_ago(1), interval_seconds=0, now=_NOW) is HeartbeatStatus.CONNECTED


def test_larger_interval_widens_connected_band():
    # 90s old with a 300s interval is still connected (it wouldn't be
    # at a 30s interval) — proves the band scales with cadence.
    assert resolve(last_heartbeat_at=_ago(90), interval_seconds=300, now=_NOW) is HeartbeatStatus.CONNECTED
    assert resolve(last_heartbeat_at=_ago(90), interval_seconds=30, now=_NOW) is HeartbeatStatus.OFFLINE


# ---- clock skew ---------------------------------------------------


def test_future_heartbeat_clamps_to_zero_age():
    # Agent clock a touch ahead — age clamps to 0, status connected.
    future = _NOW + dt.timedelta(seconds=5)
    assert heartbeat_age_seconds(last_heartbeat_at=future, now=_NOW) == 0.0
    assert resolve(last_heartbeat_at=future, interval_seconds=30, now=_NOW) is HeartbeatStatus.CONNECTED


# ---- is_live helper -----------------------------------------------


def test_is_live_true_for_connected_and_degraded():
    assert is_live(HeartbeatStatus.CONNECTED) is True
    assert is_live(HeartbeatStatus.DEGRADED) is True


def test_is_live_false_for_offline_and_never_seen():
    assert is_live(HeartbeatStatus.OFFLINE) is False
    assert is_live(HeartbeatStatus.NEVER_SEEN) is False
