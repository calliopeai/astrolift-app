"""
Tiny Prometheus PromQL client for the ``astroliftAppMetrics``
resolver (#297).

Reads a cluster's Prometheus / OTel-Prometheus-compatible endpoint
over HTTP. We only need ``query`` and ``query_range`` for the
metrics surface; richer features (admin alerts API, raw label
listing) belong in a dedicated PromQL service.

Network: ``urllib.request`` — no new dependency. Failures map to
:class:`PrometheusUnavailable` so the resolver can fall back to
synthetic data without breaking the UI.

Cache: a tiny in-memory TTL cache keyed by (endpoint, query,
time-window) protects Prometheus from hammering when many UI panels
hit the same series within the cache window.
"""

from __future__ import annotations

import dataclasses
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping

# --- errors -----------------------------------------------------------


class PrometheusError(Exception):
    """Base for any failure querying Prometheus. Resolver catches
    this and falls back to synthetic data."""


class PrometheusUnavailable(PrometheusError):
    """Network / DNS / connect / 5xx failure — the endpoint is
    unreachable right now."""


class PrometheusQueryError(PrometheusError):
    """The query itself failed (400/422 + Prometheus error body)."""


# --- PromQL label-value sanitization ----------------------------------

# Same allow-list as ``astrolift_operations.metrics_logs_api`` —
# enforced here too so a rogue caller can't inject quotes or backslashes
# into the PromQL string.
_LABEL_VALUE_RE = re.compile(r"^[A-Za-z0-9_\-./:]+$")


def sanitize_label_value(value: str) -> str:
    """Reject values that would let a caller break out of the PromQL
    label match. Returns ``value`` unchanged on success."""
    if not isinstance(value, str) or not _LABEL_VALUE_RE.match(value):
        raise PrometheusQueryError(
            f"label value {value!r} contains characters that aren't allowed in PromQL label matches"
        )
    return value


# --- in-memory cache --------------------------------------------------


@dataclasses.dataclass(slots=True)
class _CacheEntry:
    value: object
    expires_at: float


class _TTLCache:
    """Process-local, thread-safe-enough (GIL) TTL cache. Sized to
    keep the metrics surface cheap when 20+ UI panels poll once a
    minute; eviction is purely TTL-driven (no LRU)."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._items: dict[tuple, _CacheEntry] = {}

    def get(self, key: tuple) -> object | None:
        entry = self._items.get(key)
        if entry is None:
            return None
        if entry.expires_at < time.monotonic():
            self._items.pop(key, None)
            return None
        return entry.value

    def set(self, key: tuple, value: object) -> None:
        self._items[key] = _CacheEntry(
            value=value,
            expires_at=time.monotonic() + self._ttl,
        )

    def clear(self) -> None:
        self._items.clear()


# Spec 08 §6: 30s window keeps Prometheus from being hammered by every
# UI tab rendering the same dashboard.
_DEFAULT_TTL_SECONDS = 30.0
_cache = _TTLCache(ttl_seconds=_DEFAULT_TTL_SECONDS)


def clear_cache_for_tests() -> None:
    """Test-only hook so each test gets a fresh cache slice."""
    _cache.clear()


# --- HTTP -------------------------------------------------------------


def _request_json(url: str, *, timeout: float) -> Mapping:
    """Issue a GET, parse the body as JSON, return the decoded payload.

    HTTP error codes map to PrometheusQueryError (4xx) or
    PrometheusUnavailable (5xx + network failures) so the resolver
    can branch on whether to retry vs fall back.
    """
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "astrolift-metrics",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        if 500 <= exc.code < 600:
            raise PrometheusUnavailable(f"prometheus returned {exc.code}: {body}") from exc
        raise PrometheusQueryError(f"prometheus returned {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise PrometheusUnavailable(f"couldn't reach prometheus: {exc.reason}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PrometheusQueryError(f"prometheus returned non-JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise PrometheusQueryError("prometheus returned a non-object body")
    if payload.get("status") != "success":
        # Prometheus returns 200 with {"status":"error", "error": "..."}
        # on PromQL parse errors. Treat as a query error so callers
        # know it's their PromQL, not the endpoint.
        msg = payload.get("error") or "prometheus reported error status"
        raise PrometheusQueryError(str(msg))
    return payload


# --- public client ----------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class RangeQueryResult:
    """One time-series result row from ``query_range``."""

    metric_labels: Mapping[str, str]
    values: tuple[tuple[float, float], ...]
    """List of (unix_seconds, value) points."""


def query_instant(
    *,
    endpoint: str,
    query: str,
    timeout: float = 5.0,
) -> float:
    """``GET /api/v1/query?query=<expr>`` — returns the scalar value
    or the sum of the result vector. Raises on no result.

    Used for the scalar fields on AppMetricsType (request_rate
    average, error_rate, latency percentiles)."""
    base = endpoint.rstrip("/")
    qs = urllib.parse.urlencode({"query": query})
    url = f"{base}/api/v1/query?{qs}"
    cached = _cache.get(("instant", base, query))
    if cached is not None:
        return float(cached)  # type: ignore[arg-type]

    payload = _request_json(url, timeout=timeout)
    data = payload.get("data") or {}
    result = data.get("result") or []
    result_type = data.get("resultType")
    value: float
    if result_type == "scalar":
        # scalar shape: [unix, "value"]
        if not isinstance(result, list) or len(result) != 2:
            raise PrometheusQueryError("scalar result malformed")
        value = float(result[1])
    elif result_type == "vector":
        if not result:
            value = 0.0
        else:
            # Sum across all returned series — caller is responsible for
            # making sure the PromQL aggregates correctly. Multiple
            # series almost always mean the by-clause is missing.
            total = 0.0
            for row in result:
                v = row.get("value")
                if not isinstance(v, list) or len(v) != 2:
                    raise PrometheusQueryError("vector value entry malformed")
                total += float(v[1])
            value = total
    else:
        raise PrometheusQueryError(f"unexpected resultType: {result_type!r}")
    _cache.set(("instant", base, query), value)
    return value


def query_range(
    *,
    endpoint: str,
    query: str,
    start_unix: int,
    end_unix: int,
    step_seconds: int,
    timeout: float = 5.0,
) -> tuple[RangeQueryResult, ...]:
    """``GET /api/v1/query_range`` over (start, end, step)."""
    if step_seconds <= 0:
        raise PrometheusQueryError("step_seconds must be positive")
    if end_unix <= start_unix:
        raise PrometheusQueryError("end_unix must be > start_unix")

    base = endpoint.rstrip("/")
    qs = urllib.parse.urlencode(
        {
            "query": query,
            "start": str(start_unix),
            "end": str(end_unix),
            "step": str(step_seconds),
        }
    )
    url = f"{base}/api/v1/query_range?{qs}"
    cache_key = ("range", base, query, start_unix, end_unix, step_seconds)
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    payload = _request_json(url, timeout=timeout)
    data = payload.get("data") or {}
    result_type = data.get("resultType")
    if result_type != "matrix":
        raise PrometheusQueryError(f"expected matrix resultType, got {result_type!r}")
    result = data.get("result") or []
    if not isinstance(result, list):
        raise PrometheusQueryError("matrix result is not a list")

    out: list[RangeQueryResult] = []
    for row in result:
        labels = row.get("metric") or {}
        values = row.get("values") or []
        if not isinstance(values, list):
            raise PrometheusQueryError("matrix values is not a list")
        parsed: list[tuple[float, float]] = []
        for entry in values:
            if not isinstance(entry, list) or len(entry) != 2:
                raise PrometheusQueryError("matrix value entry malformed")
            ts, v = entry
            try:
                parsed.append((float(ts), float(v)))
            except (TypeError, ValueError) as exc:
                # Prometheus emits "NaN" as a string sometimes; treat
                # as 0 rather than failing the whole query.
                try:
                    parsed.append((float(ts), 0.0))
                except (TypeError, ValueError):
                    raise PrometheusQueryError(f"matrix value parse failed: {exc}") from exc
        out.append(
            RangeQueryResult(
                metric_labels=dict(labels),
                values=tuple(parsed),
            )
        )
    tup = tuple(out)
    _cache.set(cache_key, tup)
    return tup


def sum_range_to_buckets(
    rows: Iterable[RangeQueryResult],
    *,
    bucket_count: int,
    start_unix: int,
    end_unix: int,
) -> list[float]:
    """Sum every row's values into ``bucket_count`` equal-width
    time buckets between ``start_unix`` and ``end_unix``.

    Useful when the PromQL aggregates over labels (one series back)
    but the UI wants a fixed-width bucket array regardless of step.
    """
    if bucket_count <= 0:
        raise PrometheusQueryError("bucket_count must be positive")
    if end_unix <= start_unix:
        raise PrometheusQueryError("end_unix must be > start_unix")
    bucket_width = (end_unix - start_unix) / bucket_count
    buckets = [0.0] * bucket_count
    counts = [0] * bucket_count
    for row in rows:
        for ts, value in row.values:
            idx = int((ts - start_unix) / bucket_width)
            if idx < 0:
                idx = 0
            if idx >= bucket_count:
                idx = bucket_count - 1
            buckets[idx] += value
            counts[idx] += 1
    # Average over the per-bucket sample count so callers get a
    # rate-shaped series, not a sum-shaped one. Empty buckets stay 0.
    out: list[float] = []
    for total, count in zip(buckets, counts, strict=True):
        out.append(total / count if count else 0.0)
    return out
