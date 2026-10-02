"""
Tiny Prometheus PromQL client for the ``astroliftAppMetrics``
resolver (#297).

Reads a cluster's Prometheus / OTel-Prometheus-compatible endpoint
over HTTP. We only need ``query`` and ``query_range`` for the
metrics surface; richer features (admin alerts API, raw label
listing) belong in a dedicated PromQL service.

Network: ``urllib.request`` — no new dependency. Failures map to
:class:`PrometheusUnavailable` so callers can report unavailable observations.

Cache: a tiny in-memory TTL cache keyed by (endpoint, query,
time-window) protects Prometheus from hammering when many UI panels
hit the same series within the cache window.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping

# --- errors -----------------------------------------------------------


class PrometheusError(Exception):
    """Base for failures querying Prometheus; callers choose their error state."""


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


# --- AWS SigV4 signing (Amazon Managed Prometheus) --------------------
#
# Amazon Managed Prometheus (AMP) rejects unsigned reads with 403: the
# ``aps-workspaces.<region>.amazonaws.com`` query API requires SigV4 with
# the ``aps`` service (``aps:QueryMetrics``). A self-hosted Prometheus
# wants a plain GET, so we sign ONLY when the endpoint is an AWS host, or
# when the operator forces it with ``prometheus_auth="sigv4"`` (e.g. AMP
# reached through a non-``amazonaws.com`` name). Credentials come from the
# ambient botocore chain — the control-plane task role via IRSA / instance
# role — and are never hardcoded.

_AWS_HOST_SUFFIX = ".amazonaws.com"


def _endpoint_host(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def _should_sign_sigv4(host: str, auth: str | None) -> bool:
    """Sign when the operator explicitly asked for SigV4, or the endpoint
    is an AWS host (AMP). Any other host keeps the unauthenticated path."""
    if auth == "sigv4":
        return True
    return host.endswith(_AWS_HOST_SUFFIX)


def _amp_region_from_host(host: str) -> str:
    """Parse the AWS region out of an AMP endpoint host.

    AMP endpoints are ``aps-workspaces.<region>.amazonaws.com`` — like any
    AWS regional service host ``<service>.<region>.amazonaws.com`` — so the
    region is the label before ``amazonaws.com``. For a non-standard host
    the operator flagged ``sigv4`` on, fall back to the ambient
    ``AWS_REGION`` / ``AWS_DEFAULT_REGION``.
    """
    parts = host.split(".")
    if len(parts) >= 4 and parts[-2] == "amazonaws" and parts[-1] == "com":
        return parts[-3]
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if region:
        return region
    raise PrometheusUnavailable(f"can't determine AWS region for SigV4 signing from host {host!r}")


def _sign_request_sigv4(req: urllib.request.Request, *, host: str) -> None:
    """Add SigV4 auth headers to ``req`` in place for the ``aps`` service.

    Signs the request as-built (method + full URL incl. query string +
    body), so both the current GET (query in the URL) and a future POST
    (query in the body) sign correctly. A missing credential chain maps to
    PrometheusUnavailable so the resolver degrades to the empty state
    instead of raising an unhandled error.
    """
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.session import Session

    credentials = Session().get_credentials()
    if credentials is None:
        raise PrometheusUnavailable(
            "no AWS credentials available to sign the Amazon Managed Prometheus request"
        )
    region = _amp_region_from_host(host)
    aws_request = AWSRequest(
        method=req.get_method(), url=req.full_url, data=req.data, headers=dict(req.header_items())
    )
    SigV4Auth(credentials, "aps", region).add_auth(aws_request)
    for header, value in aws_request.headers.items():
        req.add_unredirected_header(header, value)


# --- HTTP -------------------------------------------------------------


def _request_json(
    url: str,
    *,
    timeout: float,
    auth: str | None = None,
    max_response_bytes: int | None = None,
    form_data: bytes | None = None,
) -> Mapping:
    """Issue a GET or form POST, parse JSON, return the decoded payload.

    HTTP error codes map to PrometheusQueryError (4xx) or
    PrometheusUnavailable (5xx + network failures) so the resolver
    can branch on whether to retry vs fall back.

    ``auth`` gates SigV4 signing alongside host detection (see
    :func:`_should_sign_sigv4`): AMP hosts are signed automatically, and a
    caller may force it with ``auth="sigv4"``.
    """
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "astrolift-metrics",
        },
        data=form_data,
    )
    if form_data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    host = _endpoint_host(url)
    if _should_sign_sigv4(host, auth):
        _sign_request_sigv4(req, host=host)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(max_response_bytes + 1) if max_response_bytes is not None else resp.read()
            if max_response_bytes is not None and len(raw) > max_response_bytes:
                raise PrometheusQueryError("prometheus response exceeds the byte limit")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read(200).decode("utf-8", "replace")[:200]
        except Exception:
            pass
        if 500 <= exc.code < 600:
            raise PrometheusUnavailable(f"prometheus returned {exc.code}: {body}") from exc
        raise PrometheusQueryError(f"prometheus returned {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise PrometheusUnavailable(f"couldn't reach prometheus: {exc.reason}") from exc
    except OSError as exc:
        raise PrometheusUnavailable("prometheus transport failed") from exc

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


def metric_names(
    *,
    endpoint: str,
    match: str,
    start_unix: int,
    end_unix: int,
    limit: int,
    timeout: float = 5.0,
    auth: str | None = None,
) -> tuple[list[str], bool]:
    """``GET /api/v1/label/__name__/values`` restricted by ``match`` (#1226).

    Returns ``(names, truncated)`` — names sorted, and a flag saying the
    series set was larger than ``limit``.

    ``match`` is a PromQL selector such as ``{namespace="astrolift-shop"}``.
    It is what keeps this scoped: without it Prometheus enumerates every
    metric name in the cluster, which for an app-scoped panel is both wrong
    and enormous.

    ``limit`` is applied here rather than in the caller so that the cap is
    part of the read. A mis-instrumented app emitting tens of thousands of
    names would otherwise be truncated only after the whole list crossed
    the wire and was parsed.
    """
    base = endpoint.rstrip("/")
    qs = urllib.parse.urlencode(
        {
            "match[]": match,
            "start": str(int(start_unix)),
            "end": str(int(end_unix)),
        }
    )
    url = f"{base}/api/v1/label/__name__/values?{qs}"

    cache_key = ("names", base, match, int(start_unix), int(end_unix), int(limit))
    cached = _cache.get(cache_key)
    if cached is not None:
        names, truncated = cached  # type: ignore[misc]
        return list(names), bool(truncated)

    payload = _request_json(url, timeout=timeout, auth=auth)
    data = payload.get("data")
    if not isinstance(data, list):
        raise PrometheusQueryError("label values result malformed")

    found = sorted({str(v) for v in data if isinstance(v, str) and v})
    truncated = len(found) > limit
    names = found[:limit]

    _cache.set(cache_key, (tuple(names), truncated))
    return names, truncated


def query_instant(
    *,
    endpoint: str,
    query: str,
    timeout: float = 5.0,
    auth: str | None = None,
) -> float:
    """``GET /api/v1/query?query=<expr>`` — returns the scalar value
    or the sum of the result vector. Raises on no result.

    Used for the scalar fields on AppMetricsType (request_rate
    average, error_rate, latency percentiles).

    ``auth`` forwards to the transport for SigV4 gating (AMP endpoints
    are signed automatically; pass ``"sigv4"`` to force it)."""
    base = endpoint.rstrip("/")
    qs = urllib.parse.urlencode({"query": query})
    url = f"{base}/api/v1/query?{qs}"
    cached = _cache.get(("instant", base, query))
    if cached is not None:
        return float(cached)  # type: ignore[arg-type]

    payload = _request_json(url, timeout=timeout, auth=auth)
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
    auth: str | None = None,
    strict: bool = False,
    max_series: int | None = None,
    max_samples: int | None = None,
    max_response_bytes: int | None = None,
    request_method: str = "GET",
    max_request_bytes: int | None = None,
) -> tuple[RangeQueryResult, ...]:
    """``GET`` or form ``POST /api/v1/query_range`` over (start, end, step).

    ``auth`` forwards to the transport for SigV4 gating (see
    :func:`query_instant`)."""
    if step_seconds <= 0:
        raise PrometheusQueryError("step_seconds must be positive")
    if end_unix <= start_unix:
        raise PrometheusQueryError("end_unix must be > start_unix")
    if request_method not in {"GET", "POST"}:
        raise PrometheusQueryError("range query supports GET or POST only")

    base = endpoint.rstrip("/")
    qs = urllib.parse.urlencode(
        {
            "query": query,
            "start": str(start_unix),
            "end": str(end_unix),
            "step": str(step_seconds),
        }
    )
    encoded = qs.encode("utf-8")
    if max_request_bytes is not None and len(encoded) > max_request_bytes:
        raise PrometheusQueryError("prometheus request exceeds the byte limit")
    url = f"{base}/api/v1/query_range" + (f"?{qs}" if request_method == "GET" else "")
    cache_key = (
        "range",
        base,
        query,
        start_unix,
        end_unix,
        step_seconds,
        strict,
        max_series,
        max_samples,
        max_response_bytes,
        auth,
        request_method,
        max_request_bytes,
    )
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    request_options = {"form_data": encoded} if request_method == "POST" else {}
    payload = _request_json(
        url, timeout=timeout, auth=auth, max_response_bytes=max_response_bytes, **request_options
    )
    # A successful response can still describe a partial/uncertain query.
    # Strict evidence must be complete; preserve the legacy non-strict path.
    if strict and "warnings" in payload:
        warnings = payload["warnings"]
        if not isinstance(warnings, list) or warnings:
            raise PrometheusQueryError("prometheus response completeness is uncertain")
    data = payload.get("data") or {}
    if not isinstance(data, Mapping):
        raise PrometheusQueryError("matrix data is not an object")
    result_type = data.get("resultType")
    if result_type != "matrix":
        raise PrometheusQueryError(f"expected matrix resultType, got {result_type!r}")
    result = data.get("result") if strict else data.get("result") or []
    if not isinstance(result, list):
        raise PrometheusQueryError("matrix result is not a list")

    if max_series is not None and len(result) > max_series:
        raise PrometheusQueryError("matrix exceeds the series limit")
    out: list[RangeQueryResult] = []
    for row in result:
        if not isinstance(row, Mapping):
            raise PrometheusQueryError("matrix row is not an object")
        labels = row.get("metric") if strict else row.get("metric") or {}
        values = row.get("values") if strict else row.get("values") or []
        if not isinstance(values, list):
            raise PrometheusQueryError("matrix values is not a list")
        if not isinstance(labels, Mapping) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in labels.items()
        ):
            raise PrometheusQueryError("matrix labels are malformed")
        if max_samples is not None and len(values) > max_samples:
            raise PrometheusQueryError("matrix exceeds the sample limit")
        parsed: list[tuple[float, float]] = []
        for entry in values:
            if not isinstance(entry, list) or len(entry) != 2:
                raise PrometheusQueryError("matrix value entry malformed")
            ts, v = entry
            try:
                timestamp, value = float(ts), float(v)
                if strict and (
                    not math.isfinite(timestamp)
                    or not math.isfinite(value)
                    or value < 0
                    or not start_unix <= timestamp <= end_unix
                ):
                    raise PrometheusQueryError("matrix contains invalid rate readings")
                if strict and parsed and timestamp <= parsed[-1][0]:
                    raise PrometheusQueryError("matrix timestamps are not increasing")
                parsed.append((timestamp, value))
            except (TypeError, ValueError) as exc:
                if strict:
                    raise PrometheusQueryError("matrix value parse failed") from exc
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
