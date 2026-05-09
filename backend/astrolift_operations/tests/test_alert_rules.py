"""Tests for default alert rules + delivery routing (#159)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from astrolift_operations.alert_rules import (
    DEFAULT_RULES,
    ChannelRoute,
    DeliveryDecision,
    Severity,
    SuppressionWindow,
    evaluate,
    render_for_app,
    should_fire,
)


UTC = timezone.utc


def _now(*, off: int = 0) -> datetime:
    return datetime(2026, 5, 9, 12, tzinfo=UTC) + timedelta(seconds=off)


# ---- default rule templates ------------------------------------------


def test_default_rule_set_matches_spec_08():
    slugs = {r.slug for r in DEFAULT_RULES}
    assert slugs == {
        "high_5xx_rate",
        "high_latency_p99",
        "pod_restart_loop",
        "oom_killed",
    }


def test_render_substitutes_app_and_namespace():
    rules = render_for_app(app_name="api", namespace="acme-prod")
    rendered = {r.slug: r for r in rules}
    assert '"api"' in rendered["high_5xx_rate"].expression
    assert '"acme-prod"' in rendered["pod_restart_loop"].expression
    # Templates leave $app / $ns gone after rendering
    for r in rules:
        assert "$app" not in r.expression
        assert "$ns" not in r.expression


def test_disabled_returns_empty():
    """Org or app-level opt-out: caller skips seeding entirely."""
    assert render_for_app(
        app_name="api", namespace="acme-prod", auto_create_disabled=True
    ) == []


def test_render_preserves_severity_and_for_window():
    rules = render_for_app(app_name="api", namespace="acme-prod")
    by_slug = {r.slug: r for r in rules}
    assert by_slug["high_5xx_rate"].severity == Severity.CRITICAL
    assert by_slug["high_5xx_rate"].for_seconds == 300
    assert by_slug["high_latency_p99"].severity == Severity.WARNING


# ---- suppression -----------------------------------------------------


def test_suppression_window_rejects_inverted_dates():
    with pytest.raises(ValueError):
        SuppressionWindow(
            starts_at=_now(off=100), ends_at=_now(off=10),
        )


def test_suppression_window_rejects_naive():
    with pytest.raises(ValueError):
        SuppressionWindow(
            starts_at=datetime(2026, 5, 9), ends_at=_now(off=100),
        )


def test_alert_inside_suppression_window_is_suppressed():
    routes = [ChannelRoute(severity=Severity.CRITICAL, channel_ids=(1, 2))]
    sup = [SuppressionWindow(starts_at=_now(off=-60), ends_at=_now(off=60))]
    out = evaluate(
        severity=Severity.CRITICAL, fired_at=_now(),
        app_name="api", routes=routes, suppressions=sup,
    )
    assert out.suppressed is True
    assert out.suppressed_reason == "maintenance window"
    assert should_fire(out) is False
    # Still records intended channels — UI surfaces 'would have fired'
    assert out.channel_ids == (1, 2)


def test_suppression_can_scope_by_app():
    routes = [ChannelRoute(severity=Severity.CRITICAL, channel_ids=(1,))]
    sup = [SuppressionWindow(
        starts_at=_now(off=-60), ends_at=_now(off=60), app_name="other-app",
    )]
    out = evaluate(
        severity=Severity.CRITICAL, fired_at=_now(),
        app_name="api", routes=routes, suppressions=sup,
    )
    # Suppression applies to other-app, not 'api'
    assert out.suppressed is False


def test_suppression_can_scope_by_severity():
    routes = [ChannelRoute(severity=Severity.WARNING, channel_ids=(1,))]
    sup = [SuppressionWindow(
        starts_at=_now(off=-60), ends_at=_now(off=60),
        severity=Severity.CRITICAL,
    )]
    out = evaluate(
        severity=Severity.WARNING, fired_at=_now(),
        app_name="api", routes=routes, suppressions=sup,
    )
    # CRITICAL-only window doesn't catch WARNING alerts
    assert out.suppressed is False


# ---- routing ---------------------------------------------------------


def test_severity_routes_to_matching_channels():
    routes = [
        ChannelRoute(severity=Severity.CRITICAL, channel_ids=(10,)),
        ChannelRoute(severity=Severity.WARNING, channel_ids=(20, 21)),
    ]
    out = evaluate(
        severity=Severity.WARNING, fired_at=_now(),
        app_name="api", routes=routes,
    )
    assert out.channel_ids == (20, 21)
    assert should_fire(out) is True


def test_no_route_for_severity_returns_empty():
    routes = [ChannelRoute(severity=Severity.CRITICAL, channel_ids=(10,))]
    out = evaluate(
        severity=Severity.WARNING, fired_at=_now(),
        app_name="api", routes=routes,
    )
    assert out.channel_ids == ()
    assert should_fire(out) is False


# ---- de-dup ---------------------------------------------------------


def test_dedup_within_window_marks_deduped():
    routes = [ChannelRoute(severity=Severity.WARNING, channel_ids=(20,))]
    out = evaluate(
        severity=Severity.WARNING, fired_at=_now(),
        app_name="api", routes=routes,
        last_delivery_at=_now(off=-60),
        dedup_window_seconds=300,
    )
    assert out.deduped is True
    assert should_fire(out) is False


def test_dedup_outside_window_fires():
    routes = [ChannelRoute(severity=Severity.WARNING, channel_ids=(20,))]
    out = evaluate(
        severity=Severity.WARNING, fired_at=_now(),
        app_name="api", routes=routes,
        last_delivery_at=_now(off=-3600),  # 1 hour ago
        dedup_window_seconds=300,
    )
    assert out.deduped is False
    assert should_fire(out) is True


def test_suppression_takes_precedence_over_dedup():
    """Maintenance window wins: an alert inside a suppression window
    is reported as suppressed, not deduped, even if it'd also be
    deduped — the operator semantics are different."""
    routes = [ChannelRoute(severity=Severity.WARNING, channel_ids=(20,))]
    sup = [SuppressionWindow(starts_at=_now(off=-60), ends_at=_now(off=60))]
    out = evaluate(
        severity=Severity.WARNING, fired_at=_now(),
        app_name="api", routes=routes, suppressions=sup,
        last_delivery_at=_now(off=-30),  # would also dedupe
        dedup_window_seconds=300,
    )
    assert out.suppressed is True
    assert out.deduped is False
