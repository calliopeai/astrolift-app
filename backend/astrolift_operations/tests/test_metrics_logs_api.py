"""Tests for metrics + log API contract (#157, spec 08 §6-§7)."""

from __future__ import annotations

import pytest

from astrolift_operations.metrics_logs_api import (
    DEFAULT_LOG_LINES_PER_SECOND,
    REQUIRED_METRIC_LABELS,
    ROLLUP_RESOLUTIONS_SECONDS,
    FilterError,
    LogFilter,
    MetricsQuery,
    RateLimitState,
    Severity,
    StandardMetric,
    consume_tokens,
    initial_bucket,
    refill_tokens,
)


# ---- standard metric vocabulary ------------------------------------


def test_standard_metric_set_locked():
    """Lock the four golden signals + saturation. Dashboards and
    alert rules depend on these strings."""
    expected = {
        "request_rate", "latency_p50", "latency_p95", "latency_p99",
        "error_rate", "saturation",
    }
    assert {m.value for m in StandardMetric} == expected


def test_required_labels_locked():
    """Per-tenant isolation depends on every metric carrying these.
    Removing one would let one tenant query another tenant's data."""
    assert REQUIRED_METRIC_LABELS == {
        "app", "workload", "environment", "region",
    }


def test_rollup_resolutions_match_spec_08():
    assert ROLLUP_RESOLUTIONS_SECONDS == (0, 60, 300, 3600)


# ---- MetricsQuery validation ---------------------------------------


def test_metrics_query_requires_app_slug():
    """No app slug = no tenant scoping. Reject."""
    with pytest.raises(FilterError, match="tenant isolation"):
        MetricsQuery(metric=StandardMetric.REQUEST_RATE, app_slug="")


def test_metrics_query_rejects_unsafe_label_value():
    """A label value with quotes/braces could break PromQL escapes."""
    with pytest.raises(FilterError):
        MetricsQuery(
            metric=StandardMetric.REQUEST_RATE,
            app_slug='api"} or 1=1 -- ',
        )


def test_metrics_query_rejects_unsafe_extra_label():
    with pytest.raises(FilterError):
        MetricsQuery(
            metric=StandardMetric.REQUEST_RATE,
            app_slug="api",
            extra_labels=(("status", '5xx" or 1=1'),),
        )


def test_metrics_query_rejects_invalid_label_name():
    with pytest.raises(FilterError):
        MetricsQuery(
            metric=StandardMetric.REQUEST_RATE,
            app_slug="api",
            extra_labels=(("9bad", "ok"),),  # leading digit
        )


def test_metrics_query_rejects_zero_step():
    with pytest.raises(FilterError):
        MetricsQuery(
            metric=StandardMetric.REQUEST_RATE,
            app_slug="api", step_seconds=0,
        )


def test_metrics_query_rejects_inverted_time_range():
    with pytest.raises(FilterError):
        MetricsQuery(
            metric=StandardMetric.REQUEST_RATE,
            app_slug="api", start_unix=200, end_unix=100,
        )


def test_metrics_query_clean_passes():
    q = MetricsQuery(
        metric=StandardMetric.LATENCY_P99, app_slug="api",
        workload="web", environment="prod", region="us-east-1",
        extra_labels=(("status", "5xx"), ("method", "GET")),
    )
    assert q.metric == StandardMetric.LATENCY_P99


# ---- LogFilter validation ------------------------------------------


def test_log_filter_requires_app_slug():
    with pytest.raises(FilterError, match="tenant isolation"):
        LogFilter(app_slug="")


def test_log_filter_rejects_newline_or_quote_in_search():
    """The driver translates search to a Loki regex / OpenSearch
    text query; embedded quotes/newlines could break the escape."""
    with pytest.raises(FilterError, match="forbidden"):
        LogFilter(app_slug="api", search='"; DROP TABLE')
    with pytest.raises(FilterError, match="forbidden"):
        LogFilter(app_slug="api", search="line1\nline2")


def test_log_filter_clean_passes():
    f = LogFilter(
        app_slug="api", workload="web", pod="api-abc",
        container="server", environment="prod",
        min_severity=Severity.WARN,
        search="connection refused",
        follow=True,
    )
    assert f.follow is True
    assert f.min_severity == Severity.WARN


def test_log_filter_rejects_inverted_time_range():
    with pytest.raises(FilterError):
        LogFilter(app_slug="api", start_unix=200, end_unix=100)


# ---- token bucket --------------------------------------------------


def test_initial_bucket_full():
    bucket = initial_bucket(capacity=1000, now_unix=100.0)
    assert bucket.tokens == 1000.0
    assert bucket.last_refill_unix == 100.0


def test_consume_succeeds_when_tokens_available():
    bucket = initial_bucket(capacity=1000, now_unix=100.0)
    ok, new = consume_tokens(bucket, n=500)
    assert ok is True
    assert new.tokens == 500


def test_consume_fails_when_insufficient():
    bucket = initial_bucket(capacity=10, now_unix=0.0)
    ok, _ = consume_tokens(bucket, n=11)
    assert ok is False


def test_refill_adds_proportional_tokens():
    bucket = RateLimitState(tokens=0.0, last_refill_unix=0.0)
    refilled = refill_tokens(
        bucket, capacity=1000, refill_per_sec=100, now_unix=2.0,
    )
    # 2 seconds * 100 = 200 tokens
    assert refilled.tokens == 200.0
    assert refilled.last_refill_unix == 2.0


def test_refill_caps_at_capacity():
    bucket = RateLimitState(tokens=900.0, last_refill_unix=0.0)
    refilled = refill_tokens(
        bucket, capacity=1000, refill_per_sec=100, now_unix=10.0,
    )
    # Would be 900 + 1000 = 1900, capped at 1000
    assert refilled.tokens == 1000.0


def test_refill_no_op_when_clock_goes_backward():
    """NTP step backwards must not refund tokens (would let a
    consumer burst beyond the limit)."""
    bucket = RateLimitState(tokens=500.0, last_refill_unix=100.0)
    refilled = refill_tokens(
        bucket, capacity=1000, refill_per_sec=100, now_unix=50.0,
    )
    assert refilled.tokens == 500.0


def test_default_rate_limit_matches_spec():
    assert DEFAULT_LOG_LINES_PER_SECOND == 1000
