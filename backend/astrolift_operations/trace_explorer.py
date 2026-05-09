"""
Trace explorer API contract (#158, spec 08 §8).

Pure-Python module. Real Tempo/Jaeger queries live in driver
land (``TraceDriver.search`` / ``get_trace`` from #11); this
module owns:

* The **search filter** with tenant scope + tag validation +
  pagination contract.
* **Span tree analysis** on a fetched trace: critical path
  (longest chain of dependent spans), per-span self-time
  (own work minus child time), trace comparison primitives.
* **Log cross-link** materializer — given a trace_id and
  optional span_id, build the deterministic LogFilter (#157)
  that drills from the trace to the matching log lines.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from enum import Enum

from astrolift_operations.metrics_logs_api import LogFilter, Severity

_TAG_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.\-]*$")
_LABEL_VALUE_RE = re.compile(r"^[A-Za-z0-9_\-./:]+$")
# Operation / service names in OTLP are free-form (e.g.
# 'GET /users/:id'). Permit space + a small extra-char set, but
# reject quotes / control chars that could break query escapes.
_OP_NAME_RE = re.compile(r"^[A-Za-z0-9_\-./:?# ]+$")


class TraceFilterError(ValueError):
    pass


# ---- search filter --------------------------------------------------


class SpanStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    UNSET = "unset"


@dataclasses.dataclass(frozen=True, slots=True)
class TraceSearchFilter:
    """Inputs for ``TraceDriver.search``."""

    app_slug: str
    """Tenant scope. Required. Empty rejects."""

    service: str = ""
    operation: str = ""
    status: SpanStatus | None = None
    min_duration_ms: int = 0
    max_duration_ms: int = 0
    start_unix: int = 0
    end_unix: int = 0
    tags: tuple[tuple[str, str], ...] = ()
    """Caller-supplied tag filters. Validated against character
    classes that match OTLP attribute names + safe values."""

    page_size: int = 50
    page_token: str = ""

    def __post_init__(self) -> None:
        if not self.app_slug:
            raise TraceFilterError(
                "app_slug is required (per-tenant isolation)"
            )
        if not _LABEL_VALUE_RE.match(self.app_slug):
            raise TraceFilterError(
                f"app_slug {self.app_slug!r} contains unsafe characters"
            )
        for opt in (self.service, self.operation):
            if opt and not _OP_NAME_RE.match(opt):
                raise TraceFilterError(
                    f"value {opt!r} contains unsafe characters"
                )
        for k, v in self.tags:
            if not _TAG_NAME_RE.match(k):
                raise TraceFilterError(f"tag name {k!r} invalid")
            if not _LABEL_VALUE_RE.match(v):
                raise TraceFilterError(f"tag value {v!r} unsafe")
        if self.min_duration_ms < 0 or self.max_duration_ms < 0:
            raise TraceFilterError("durations must be non-negative")
        if self.max_duration_ms and self.min_duration_ms > self.max_duration_ms:
            raise TraceFilterError(
                "min_duration_ms must be <= max_duration_ms"
            )
        if self.end_unix and self.start_unix > self.end_unix:
            raise TraceFilterError("start_unix must be <= end_unix")
        if self.page_size <= 0 or self.page_size > 1000:
            raise TraceFilterError(
                f"page_size {self.page_size} must be 1..1000"
            )


# ---- span tree analysis --------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class Span:
    span_id: str
    parent_id: str
    operation: str
    start_unix_ns: int
    end_unix_ns: int
    status: SpanStatus
    service: str = ""

    @property
    def duration_ns(self) -> int:
        return self.end_unix_ns - self.start_unix_ns


@dataclasses.dataclass(frozen=True, slots=True)
class SpanTiming:
    span_id: str
    duration_ns: int
    self_time_ns: int


def self_times(spans: Sequence[Span]) -> dict[str, int]:
    """Per-span self-time = duration - sum(child durations).

    Useful for the latency breakdown panel: spans with high
    self-time indicate where the work actually happened (vs.
    time spent waiting on downstream calls).
    """
    children: dict[str, list[Span]] = {}
    for s in spans:
        children.setdefault(s.parent_id, []).append(s)
    out: dict[str, int] = {}
    for s in spans:
        child_total = sum(c.duration_ns for c in children.get(s.span_id, []))
        out[s.span_id] = max(0, s.duration_ns - child_total)
    return out


def critical_path(spans: Sequence[Span]) -> tuple[Span, ...]:
    """Return the chain of spans from root to the leaf that
    finishes last.

    The 'critical path' for latency optimization is the chain of
    dependent spans that determines the total trace duration.
    Optimizing anything off this chain doesn't reduce total
    latency.

    For multi-rooted traces (no parent_id == ""), picks the root
    that started the latest among the roots — those represent
    new work begun late in the trace.
    """
    if not spans:
        return ()
    by_id: dict[str, Span] = {s.span_id: s for s in spans}
    children: dict[str, list[Span]] = {}
    for s in spans:
        children.setdefault(s.parent_id, []).append(s)

    roots = children.get("", []) + [
        s for s in spans
        if s.parent_id and s.parent_id not in by_id
    ]
    if not roots:
        return ()
    # Pick the root whose subtree ends LATEST (largest end_ns
    # among descendants). That subtree drove the trace duration.
    def subtree_end(span: Span) -> int:
        end = span.end_unix_ns
        for c in children.get(span.span_id, []):
            end = max(end, subtree_end(c))
        return end

    root = max(roots, key=subtree_end)

    chain: list[Span] = [root]
    current = root
    while True:
        kids = children.get(current.span_id, [])
        if not kids:
            break
        # Walk into the child whose subtree ends latest — that's
        # the one on the critical path.
        next_kid = max(kids, key=subtree_end)
        chain.append(next_kid)
        current = next_kid
    return tuple(chain)


# ---- log cross-link -------------------------------------------------


def log_filter_for_trace(
    *,
    app_slug: str,
    trace_id: str,
    span_id: str = "",
    start_unix: int = 0,
    end_unix: int = 0,
) -> LogFilter:
    """Build the LogFilter (#157) that drills from a trace into
    matching log lines. Uses the ``trace_id`` (and optionally
    ``span_id``) as a search substring; pods structurally log
    these per spec 08 §7 conventions."""
    if not trace_id:
        raise TraceFilterError("trace_id is required for log cross-link")
    search = trace_id
    if span_id:
        search = f"{trace_id} {span_id}"
    return LogFilter(
        app_slug=app_slug,
        search=search,
        min_severity=Severity.DEBUG,
        start_unix=start_unix,
        end_unix=end_unix,
    )


# ---- comparison -----------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class TraceComparison:
    """Side-by-side comparison primitives for the UI."""

    trace_a_id: str
    trace_b_id: str
    duration_a_ns: int
    duration_b_ns: int
    delta_ns: int
    """duration_b_ns - duration_a_ns; positive means B is slower."""

    common_ops: tuple[str, ...]
    a_only_ops: tuple[str, ...]
    b_only_ops: tuple[str, ...]


def compare_traces(
    *,
    trace_a_id: str, spans_a: Sequence[Span],
    trace_b_id: str, spans_b: Sequence[Span],
) -> TraceComparison:
    """Diff two traces by operation set + duration."""
    ops_a = {s.operation for s in spans_a if s.operation}
    ops_b = {s.operation for s in spans_b if s.operation}

    duration_a = (
        max(s.end_unix_ns for s in spans_a) - min(s.start_unix_ns for s in spans_a)
        if spans_a else 0
    )
    duration_b = (
        max(s.end_unix_ns for s in spans_b) - min(s.start_unix_ns for s in spans_b)
        if spans_b else 0
    )

    return TraceComparison(
        trace_a_id=trace_a_id, trace_b_id=trace_b_id,
        duration_a_ns=duration_a, duration_b_ns=duration_b,
        delta_ns=duration_b - duration_a,
        common_ops=tuple(sorted(ops_a & ops_b)),
        a_only_ops=tuple(sorted(ops_a - ops_b)),
        b_only_ops=tuple(sorted(ops_b - ops_a)),
    )
