"""Tests for per-org observability retention policy (#160)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from astrolift_operations.observability_retention import (
    ALL_STREAMS,
    PLATFORM_DEFAULTS,
    EffectiveRetention,
    RetentionHold,
    billable_window_days,
    cutoff_at,
    effective_for,
    is_held,
    warn_threshold_for,
)

UTC = UTC


# ---- effective resolution -------------------------------------------


def test_platform_defaults_match_spec_08():
    assert PLATFORM_DEFAULTS == {
        "log": 30,
        "metric_raw": 90,
        "metric_rollup": 365,
        "trace": 14,
    }


def test_no_override_uses_platform_default():
    r = effective_for(stream="log", org_override_days=None)
    assert r.days == 30
    assert r.source == "platform_default"


def test_positive_override_wins():
    r = effective_for(stream="log", org_override_days=60)
    assert r.days == 60
    assert r.source == "org_override"


def test_zero_or_negative_override_falls_back():
    """Defensive: a typo in the admin UI ('0 days') would otherwise
    silently evict every log."""
    for bad in (0, -5):
        r = effective_for(stream="log", org_override_days=bad)
        assert r.days == 30
        assert r.source == "platform_default"


def test_unknown_stream_raises():
    with pytest.raises(ValueError, match="unknown observability stream"):
        effective_for(stream="bogus", org_override_days=None)  # type: ignore[arg-type]


def test_all_streams_covered():
    """If a stream is added to the platform defaults, the resolver
    must handle it — the loop below catches a forgotten branch."""
    for s in ALL_STREAMS:
        r = effective_for(stream=s, org_override_days=None)
        assert r.days == PLATFORM_DEFAULTS[s]


# ---- cutoff arithmetic ----------------------------------------------


def test_cutoff_subtracts_days():
    r = EffectiveRetention(stream="log", days=30, source="x")
    now = datetime(2026, 5, 1, tzinfo=UTC)
    assert cutoff_at(r, now=now) == now - timedelta(days=30)


def test_cutoff_rejects_naive_now():
    r = EffectiveRetention(stream="log", days=30, source="x")
    with pytest.raises(ValueError):
        cutoff_at(r, now=datetime(2026, 5, 1))


# ---- holds ----------------------------------------------------------


def _hold(**kw):
    base = {
        "stream": "log",
        "starts_at": datetime(2026, 5, 1, tzinfo=UTC),
        "ends_at": datetime(2026, 5, 3, tzinfo=UTC),
        "resource_kind": "",
        "resource_id": "",
        "reason": "",
    }
    base.update(kw)
    return RetentionHold(**base)


def test_hold_rejects_naive_timestamps():
    with pytest.raises(ValueError):
        RetentionHold(
            stream="log",
            starts_at=datetime(2026, 5, 1),
            ends_at=datetime(2026, 5, 3, tzinfo=UTC),
        )


def test_hold_rejects_inverted_window():
    with pytest.raises(ValueError):
        RetentionHold(
            stream="log",
            starts_at=datetime(2026, 5, 3, tzinfo=UTC),
            ends_at=datetime(2026, 5, 1, tzinfo=UTC),
        )


def test_held_row_inside_window():
    holds = [_hold()]
    assert (
        is_held(
            stream="log",
            timestamp=datetime(2026, 5, 2, tzinfo=UTC),
            resource_kind="",
            resource_id="",
            holds=holds,
        )
        is True
    )


def test_held_row_at_window_boundary_inclusive():
    holds = [_hold()]
    assert (
        is_held(
            stream="log",
            timestamp=datetime(2026, 5, 1, tzinfo=UTC),
            resource_kind="",
            resource_id="",
            holds=holds,
        )
        is True
    )
    assert (
        is_held(
            stream="log",
            timestamp=datetime(2026, 5, 3, tzinfo=UTC),
            resource_kind="",
            resource_id="",
            holds=holds,
        )
        is True
    )


def test_unheld_row_outside_window():
    holds = [_hold()]
    assert (
        is_held(
            stream="log",
            timestamp=datetime(2026, 5, 4, tzinfo=UTC),
            resource_kind="",
            resource_id="",
            holds=holds,
        )
        is False
    )


def test_hold_scoped_to_resource_kind_and_id():
    holds = [_hold(resource_kind="App", resource_id="42")]
    # different kind → no hold
    assert (
        is_held(
            stream="log",
            timestamp=datetime(2026, 5, 2, tzinfo=UTC),
            resource_kind="Workload",
            resource_id="42",
            holds=holds,
        )
        is False
    )
    # same kind, different id → no hold
    assert (
        is_held(
            stream="log",
            timestamp=datetime(2026, 5, 2, tzinfo=UTC),
            resource_kind="App",
            resource_id="99",
            holds=holds,
        )
        is False
    )
    # match → held
    assert (
        is_held(
            stream="log",
            timestamp=datetime(2026, 5, 2, tzinfo=UTC),
            resource_kind="App",
            resource_id="42",
            holds=holds,
        )
        is True
    )


def test_wildcard_stream_hold_covers_any_stream():
    """A '*' hold pins every observability stream — the use case is
    'incident, hold everything'."""
    holds = [_hold(stream="*")]
    for s in ALL_STREAMS:
        assert (
            is_held(
                stream=s,
                timestamp=datetime(2026, 5, 2, tzinfo=UTC),
                resource_kind="",
                resource_id="",
                holds=holds,
            )
            is True
        )


def test_no_holds_means_unheld():
    assert (
        is_held(
            stream="log",
            timestamp=datetime(2026, 5, 2, tzinfo=UTC),
            resource_kind="",
            resource_id="",
            holds=[],
        )
        is False
    )


# ---- billing helpers ------------------------------------------------


def test_billable_window_returns_days():
    r = EffectiveRetention(stream="metric_raw", days=180, source="org_override")
    assert billable_window_days(r) == 180


def test_warn_threshold_proportional_to_window():
    short = EffectiveRetention(stream="log", days=30, source="x")
    long = EffectiveRetention(stream="metric_rollup", days=365, source="x")
    assert warn_threshold_for(short) == 7
    assert warn_threshold_for(long) == 14
    # Boundary at 30 — short retention category is inclusive
    boundary = EffectiveRetention(stream="log", days=30, source="x")
    assert warn_threshold_for(boundary) == 7
    just_over = EffectiveRetention(stream="log", days=31, source="x")
    assert warn_threshold_for(just_over) == 14
