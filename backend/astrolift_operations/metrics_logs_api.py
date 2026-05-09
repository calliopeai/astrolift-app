"""
Standard metric set + log query/stream filter contract (#157,
spec 08 §6-§7).

Pure-Python module. The actual Prometheus/Loki/OTLP queries live
in driver land (``MetricsDriver.query``, ``LogStreamDriver.stream``
on the protocol set in #11); this module owns:

* The **standard metric vocabulary** — request rate, latency
  percentiles, error rate, saturation. Locked-down strings so
  dashboards and alert rules (#159) can rely on them.
* The **label convention** — every metric carries (app, workload,
  environment, region) so per-tenant isolation is enforced at
  query time.
* **Filter validators** — the GraphQL log-stream + CLI both pass
  through ``LogFilter`` / ``MetricsQuery`` dataclasses; this
  module rejects malformed filters before they hit the driver.
* **Rate-limit policy** — per-consumer cap on log-stream
  bandwidth (spec 08 §7) so a runaway tail doesn't amplify a
  log storm.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from enum import Enum


# Spec 08 §6 — the 'four golden signals' plus saturation. Lock the
# names so PrometheusRule files + alert templates (#159) can refer
# to them by stable string.
class StandardMetric(str, Enum):
    REQUEST_RATE = "request_rate"
    LATENCY_P50 = "latency_p50"
    LATENCY_P95 = "latency_p95"
    LATENCY_P99 = "latency_p99"
    ERROR_RATE = "error_rate"
    SATURATION = "saturation"


# Spec 08 §6 retention rollups. Pure metadata for the catalog
# query; actual retention enforcement lives in #160 obs retention.
ROLLUP_RESOLUTIONS_SECONDS: tuple[int, ...] = (
    0,             # raw samples
    60,            # 1-minute rollup
    300,           # 5-minute rollup
    3600,          # 1-hour rollup
)


# Required label set (spec 08 §6). Per-tenant isolation: every
# metric MUST carry these so a rogue app can't query another
# tenant's data.
REQUIRED_METRIC_LABELS: frozenset[str] = frozenset({
    "app", "workload", "environment", "region",
})


_LABEL_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_LABEL_VALUE_RE = re.compile(r"^[A-Za-z0-9_\-./:]+$")


class FilterError(ValueError):
    pass


def _check_label_name(name: str) -> None:
    if not _LABEL_RE.match(name):
        raise FilterError(
            f"label name {name!r} is not a valid identifier "
            "(alpha/alphanumeric/underscore, alpha-start)"
        )


def _check_label_value(value: str) -> None:
    if not _LABEL_VALUE_RE.match(value):
        raise FilterError(
            f"label value {value!r} contains characters that could break "
            "PromQL injection guards"
        )


# ---- metrics query --------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class MetricsQuery:
    """A typed query the GraphQL metrics resolver passes to the
    driver. ``metric`` is one of the standard names; arbitrary
    PromQL is rejected — operators wanting raw PromQL hit a
    different (admin-only) endpoint."""

    metric: StandardMetric
    app_slug: str
    workload: str = ""
    environment: str = ""
    region: str = ""
    extra_labels: tuple[tuple[str, str], ...] = ()
    """For drill-down filtering. Caller passes (k, v) pairs;
    every name + value is validated."""

    start_unix: int = 0
    end_unix: int = 0
    step_seconds: int = 60

    def __post_init__(self) -> None:
        if not self.app_slug:
            raise FilterError("app_slug is required (per-tenant isolation)")
        _check_label_value(self.app_slug)
        for opt in (self.workload, self.environment, self.region):
            if opt:
                _check_label_value(opt)
        for k, v in self.extra_labels:
            _check_label_name(k)
            _check_label_value(v)
        if self.step_seconds <= 0:
            raise FilterError("step_seconds must be positive")
        if self.end_unix and self.start_unix > self.end_unix:
            raise FilterError("start_unix must be <= end_unix")


# ---- log filter -----------------------------------------------------


class Severity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


@dataclasses.dataclass(frozen=True, slots=True)
class LogFilter:
    """Filter for the streaming log endpoint."""

    app_slug: str
    workload: str = ""
    pod: str = ""
    container: str = ""
    environment: str = ""
    min_severity: Severity = Severity.INFO
    start_unix: int = 0
    end_unix: int = 0
    search: str = ""
    """Substring match. Driver may translate to a Loki regex /
    OpenSearch text query — no regex characters in the search
    string itself (validated)."""

    follow: bool = False

    def __post_init__(self) -> None:
        if not self.app_slug:
            raise FilterError("app_slug is required (per-tenant isolation)")
        _check_label_value(self.app_slug)
        for opt in (self.workload, self.pod, self.container, self.environment):
            if opt:
                _check_label_value(opt)
        if self.search and any(c in self.search for c in '\n\r"\''):
            raise FilterError(
                f"search string contains forbidden characters "
                "(newline / quote)"
            )
        if self.end_unix and self.start_unix > self.end_unix:
            raise FilterError("start_unix must be <= end_unix")


# ---- rate limiting --------------------------------------------------


# Spec 08 §7 — per-consumer cap. Default 1000 lines/sec is enough
# for most operators tailing one app; the limit applies to the
# stream's outbound rate so a log storm doesn't amplify.
DEFAULT_LOG_LINES_PER_SECOND = 1000


@dataclasses.dataclass(frozen=True, slots=True)
class RateLimitState:
    """Token-bucket-shaped state. Caller persists this between
    pulls (Redis or worker memory)."""

    tokens: float
    last_refill_unix: float


def refill_tokens(
    state: RateLimitState,
    *,
    capacity: int,
    refill_per_sec: int,
    now_unix: float,
) -> RateLimitState:
    """Standard token-bucket refill. Pure function so tests can
    advance time deterministically."""
    if now_unix < state.last_refill_unix:
        # Clock went backwards (NTP step) — don't refund tokens.
        return state
    elapsed = now_unix - state.last_refill_unix
    new_tokens = min(capacity, state.tokens + elapsed * refill_per_sec)
    return RateLimitState(tokens=new_tokens, last_refill_unix=now_unix)


def consume_tokens(
    state: RateLimitState,
    *,
    n: int,
) -> tuple[bool, RateLimitState]:
    """Try to consume ``n`` tokens. Returns (ok, new_state)."""
    if state.tokens < n:
        return False, state
    return True, RateLimitState(
        tokens=state.tokens - n, last_refill_unix=state.last_refill_unix,
    )


def initial_bucket(*, capacity: int, now_unix: float) -> RateLimitState:
    """Caller starts each stream with a full bucket so the first
    second isn't artificially throttled."""
    return RateLimitState(tokens=float(capacity), last_refill_unix=now_unix)
