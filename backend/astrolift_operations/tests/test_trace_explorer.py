"""Tests for trace explorer API contract (#158, spec 08 §8)."""

from __future__ import annotations

import pytest

from astrolift_operations.trace_explorer import (
    Span,
    SpanStatus,
    TraceFilterError,
    TraceSearchFilter,
    compare_traces,
    critical_path,
    log_filter_for_trace,
    self_times,
)

# ---- search filter -------------------------------------------------


def test_filter_requires_app_slug():
    with pytest.raises(TraceFilterError, match="tenant isolation"):
        TraceSearchFilter(app_slug="")


def test_filter_rejects_unsafe_app_slug():
    with pytest.raises(TraceFilterError):
        TraceSearchFilter(app_slug='api"} or 1=1 --')


def test_filter_rejects_unsafe_tag_name_or_value():
    with pytest.raises(TraceFilterError, match="tag name"):
        TraceSearchFilter(app_slug="api", tags=(("9bad", "x"),))
    with pytest.raises(TraceFilterError, match="tag value"):
        TraceSearchFilter(app_slug="api", tags=(("ok", '"unsafe"'),))


def test_filter_rejects_negative_durations():
    with pytest.raises(TraceFilterError):
        TraceSearchFilter(app_slug="api", min_duration_ms=-1)


def test_filter_rejects_inverted_duration_range():
    with pytest.raises(TraceFilterError, match="min_duration_ms"):
        TraceSearchFilter(app_slug="api", min_duration_ms=100, max_duration_ms=10)


def test_filter_rejects_inverted_time_range():
    with pytest.raises(TraceFilterError, match="start_unix"):
        TraceSearchFilter(app_slug="api", start_unix=200, end_unix=100)


def test_filter_clamps_page_size():
    with pytest.raises(TraceFilterError):
        TraceSearchFilter(app_slug="api", page_size=0)
    with pytest.raises(TraceFilterError):
        TraceSearchFilter(app_slug="api", page_size=2000)


def test_filter_clean_passes():
    """OTLP operation names are free-form (HTTP method + path).
    Permit space + path chars but still reject quotes."""
    f = TraceSearchFilter(
        app_slug="api", service="web", operation="GET /users/:id",
        status=SpanStatus.ERROR, min_duration_ms=10, max_duration_ms=1000,
        tags=(("http.status_code", "5xx"),),
        page_size=100,
    )
    assert f.status == SpanStatus.ERROR


def test_filter_rejects_quotes_in_operation():
    """Even though operation accepts spaces + paths, quotes still
    reject — would break query escapes."""
    with pytest.raises(TraceFilterError):
        TraceSearchFilter(app_slug="api", operation='GET" /users')


# ---- self_times ----------------------------------------------------


def _span(span_id: str, parent_id: str, start: int, end: int, op: str = "") -> Span:
    return Span(
        span_id=span_id, parent_id=parent_id,
        operation=op or span_id,
        start_unix_ns=start, end_unix_ns=end,
        status=SpanStatus.OK,
    )


def test_self_time_subtracts_child_durations():
    """Root 0..100. Child 10..50 (40ns). Self-time = 100 - 40 = 60."""
    spans = [
        _span("root", "", 0, 100),
        _span("child", "root", 10, 50),
    ]
    out = self_times(spans)
    assert out["root"] == 60
    assert out["child"] == 40   # leaf == its own duration


def test_self_time_floors_at_zero_for_overlapping_children():
    """If children overlap or exceed parent duration, self-time
    can't be negative — clamp at 0."""
    spans = [
        _span("root", "", 0, 100),
        _span("a", "root", 0, 80),
        _span("b", "root", 0, 80),  # parallel with a
    ]
    out = self_times(spans)
    # naive: 100 - 80 - 80 = -60 → clamped to 0
    assert out["root"] == 0


# ---- critical_path -------------------------------------------------


def test_critical_path_walks_to_latest_finishing_descendant():
    """Root 0..100. Two children: A finishes 70, B finishes 100.
    Critical path = root -> B."""
    spans = [
        _span("root", "", 0, 100),
        _span("a", "root", 0, 70),
        _span("b", "root", 0, 100),
    ]
    chain = critical_path(spans)
    chain_ids = [s.span_id for s in chain]
    assert chain_ids == ["root", "b"]


def test_critical_path_descends_through_subtree():
    spans = [
        _span("root", "", 0, 100),
        _span("a", "root", 0, 100),
        _span("a1", "a", 0, 60),
        _span("a2", "a", 60, 100),  # latest in a's subtree
    ]
    chain_ids = [s.span_id for s in critical_path(spans)]
    assert chain_ids == ["root", "a", "a2"]


def test_critical_path_empty_when_no_spans():
    assert critical_path([]) == ()


def test_critical_path_handles_orphan_parent():
    """parent_id present but unknown — treat span as a root."""
    spans = [_span("orphan", "missing-parent-id", 0, 50)]
    chain = critical_path(spans)
    assert len(chain) == 1
    assert chain[0].span_id == "orphan"


# ---- log cross-link ------------------------------------------------


def test_log_filter_uses_trace_id_as_search():
    out = log_filter_for_trace(app_slug="api", trace_id="t-abc-123")
    assert "t-abc-123" in out.search
    assert out.app_slug == "api"


def test_log_filter_includes_span_id_when_provided():
    out = log_filter_for_trace(
        app_slug="api", trace_id="t-abc", span_id="s-456",
    )
    assert "t-abc" in out.search
    assert "s-456" in out.search


def test_log_filter_requires_trace_id():
    with pytest.raises(TraceFilterError):
        log_filter_for_trace(app_slug="api", trace_id="")


# ---- comparison ----------------------------------------------------


def test_compare_traces_diffs_op_sets_and_duration():
    spans_a = [
        _span("a-root", "", 0, 100, op="GET /users"),
        _span("a-db", "a-root", 10, 60, op="db.query"),
    ]
    spans_b = [
        _span("b-root", "", 0, 200, op="GET /users"),
        _span("b-cache", "b-root", 0, 30, op="cache.lookup"),
    ]
    cmp = compare_traces(
        trace_a_id="a", spans_a=spans_a,
        trace_b_id="b", spans_b=spans_b,
    )
    assert cmp.duration_a_ns == 100
    assert cmp.duration_b_ns == 200
    assert cmp.delta_ns == 100
    assert cmp.common_ops == ("GET /users",)
    assert cmp.a_only_ops == ("db.query",)
    assert cmp.b_only_ops == ("cache.lookup",)


def test_compare_traces_handles_empty_one_side():
    spans_a = [_span("a", "", 0, 100, op="x")]
    cmp = compare_traces(
        trace_a_id="a", spans_a=spans_a,
        trace_b_id="b", spans_b=[],
    )
    assert cmp.duration_b_ns == 0
    assert cmp.delta_ns == -100  # B faster (because empty)
