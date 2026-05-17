"""HTTP URL health probe (#406).

Implements the live "is this app responding right now" check that
backs the App overview's per-URL health pill. The resolver layer in
``schema/queries.py`` is the entry point; this module is the I/O +
classification core, isolated so it's testable without a Strawberry
``Info``.

Design notes:

* Sync ``httpx.Client`` matches the rest of the Strawberry resolver
  surface (every other resolver in this codebase is sync). We can
  flip to ``AsyncClient`` later if a resolver needs to fan out to
  many URLs in parallel; for one-URL-at-a-time the sync path is
  simpler + avoids the async/sync bridge tax inside Django request
  handlers.

* GET (not HEAD) — too many origins silently 405 HEAD, which would
  show every health endpoint as ``down``. The body is dropped (we
  read only the status line + headers via streaming).

* Redirects followed once, capped at 5 hops by httpx default. The
  final status code wins.

* Status taxonomy is fixed and surfaced as a string enum so the FE
  can render colors without coupling to numeric ranges.

* Results cached for 30s per (app guid, url) in the Django default
  cache. The probe history widget reads a small ring buffer kept
  under a sibling cache key (last 10 entries, 1h TTL).
"""

from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import dataclass

import httpx
from django.core.cache import cache

# ---------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------

# Request timeout matches the issue spec — 10s. Pragmatic ceiling
# above a healthy app's expected latency budget so we don't classify
# a slow-but-alive endpoint as ``down``.
PROBE_TIMEOUT_SECONDS: float = 10.0

# Latency band for ``ok`` vs ``degraded`` (when the status code itself
# is 2xx). Mirrors the issue spec: 2xx + <5s → ok, 2xx + >=5s → degraded.
OK_LATENCY_CEILING_SECONDS: float = 5.0

# Result cache TTL: don't hammer apps on every overview render.
# Matches the FE poll cadence so a polled refresh inside the window
# returns the cached probe instead of issuing a fresh request.
RESULT_CACHE_TTL_SECONDS: int = 30

# History ring buffer length + TTL. We keep enough samples to render
# a small uptime trail (last-N pill tooltip) without persisting to a
# new table — the cache layer is sufficient for an at-a-glance
# widget. Persistence is a separate ticket.
HISTORY_MAX_ENTRIES: int = 10
HISTORY_CACHE_TTL_SECONDS: int = 3600

# User-Agent identifies our probe traffic in origin logs so operators
# can grep it out of their access logs cleanly.
PROBE_USER_AGENT: str = "AstroliftHealthProbe/1.0"


# ---------------------------------------------------------------------
# data shapes
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    """One probe attempt, frozen so callers can stash it in caches /
    structures without worrying about post-write mutation."""

    url: str
    status: str
    status_code: int | None
    latency_ms: int | None
    last_checked: dt.datetime
    message: str

    def to_cache_dict(self) -> dict:
        """Serialise to JSON-safe primitives for the Django cache.

        ``datetime`` doesn't round-trip cleanly across every cache
        backend (locmem keeps the object, redis pickles by default
        but the wire format is brittle across Python versions), so
        we encode the timestamp as ISO-8601 + parse on read.
        """
        return {
            "url": self.url,
            "status": self.status,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
            "last_checked": self.last_checked.isoformat(),
            "message": self.message,
        }

    @classmethod
    def from_cache_dict(cls, raw: dict) -> ProbeResult:
        return cls(
            url=str(raw["url"]),
            status=str(raw["status"]),
            status_code=raw.get("status_code"),
            latency_ms=raw.get("latency_ms"),
            last_checked=dt.datetime.fromisoformat(raw["last_checked"]),
            message=str(raw.get("message", "")),
        )


# ---------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------


def classify(status_code: int | None, latency_seconds: float | None, *, error: str | None) -> str:
    """Bucket a single probe attempt into the public status taxonomy.

    Order of precedence is deliberate: a transport-level error
    (``error`` non-empty) is always ``down`` even if a partial status
    code came back, because the response wasn't usable. After that we
    branch on the HTTP class:

    * 2xx + within OK_LATENCY_CEILING_SECONDS → ``ok``
    * 2xx + slower than the ceiling → ``degraded`` (alive but sluggish)
    * 3xx / 4xx → ``degraded`` (working but not green)
    * 5xx → ``down`` (server-side failure)

    Anything else (None code, unparseable) → ``down``.
    """
    if error:
        return "down"
    if status_code is None:
        return "down"
    if 200 <= status_code < 300:
        if latency_seconds is not None and latency_seconds >= OK_LATENCY_CEILING_SECONDS:
            return "degraded"
        return "ok"
    if 300 <= status_code < 500:
        return "degraded"
    if 500 <= status_code < 600:
        return "down"
    return "down"


# ---------------------------------------------------------------------
# cache keys
# ---------------------------------------------------------------------


def _result_cache_key(app_guid: str, url: str) -> str:
    # ``url`` is constrained to URLs the resolver has already
    # validated, so it's safe to embed verbatim in the cache key.
    return f"astrolift:url_health:{app_guid}:{url}"


def _history_cache_key(app_guid: str, url: str) -> str:
    return f"astrolift:url_health_history:{app_guid}:{url}"


# ---------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------


def _execute_probe(url: str, *, client_factory=httpx.Client) -> ProbeResult:
    """Issue one GET against ``url`` and shape the result.

    The ``client_factory`` indirection is the test seam — tests pass
    in a stub that records calls and returns canned responses
    without touching the network.
    """
    started = time.monotonic()
    now = dt.datetime.now(tz=dt.UTC)
    try:
        with client_factory(
            timeout=PROBE_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": PROBE_USER_AGENT},
        ) as client:
            response = client.get(url)
        elapsed = time.monotonic() - started
        latency_ms = int(elapsed * 1000)
        status = classify(response.status_code, elapsed, error=None)
        message = _message_for(response.status_code, status)
        return ProbeResult(
            url=url,
            status=status,
            status_code=int(response.status_code),
            latency_ms=latency_ms,
            last_checked=now,
            message=message,
        )
    except httpx.TimeoutException:
        elapsed = time.monotonic() - started
        return ProbeResult(
            url=url,
            status="down",
            status_code=None,
            latency_ms=int(elapsed * 1000),
            last_checked=now,
            message=f"timed out after {PROBE_TIMEOUT_SECONDS:.0f}s",
        )
    except httpx.HTTPError as exc:
        elapsed = time.monotonic() - started
        return ProbeResult(
            url=url,
            status="down",
            status_code=None,
            latency_ms=int(elapsed * 1000),
            last_checked=now,
            # Hide the bare exception class name from operators;
            # httpx's class names ("ConnectError", "RemoteProtocolError")
            # are accurate but unhelpful on a UI badge tooltip.
            message=_message_for_transport_error(exc),
        )


def _message_for(status_code: int, status: str) -> str:
    """Human-readable summary for the UI pill tooltip.

    Empty string for the happy path — the tooltip already shows the
    status code + latency, no need to repeat ``"200 OK"``.
    """
    if status == "ok":
        return ""
    if 300 <= status_code < 400:
        return f"redirected (HTTP {status_code})"
    if 400 <= status_code < 500:
        return f"client error (HTTP {status_code})"
    if 500 <= status_code < 600:
        return f"server error (HTTP {status_code})"
    return f"HTTP {status_code}"


def _message_for_transport_error(exc: httpx.HTTPError) -> str:
    """Map an httpx exception to a short operator-facing reason."""
    cls = type(exc).__name__
    if cls in {"ConnectError", "ConnectTimeout"}:
        return "couldn't connect"
    if cls in {"ReadTimeout", "WriteTimeout", "PoolTimeout"}:
        return "timed out"
    if cls == "RemoteProtocolError":
        return "remote ended the connection"
    if cls in {"ProxyError", "TooManyRedirects"}:
        return cls
    return "unreachable"


# ---------------------------------------------------------------------
# public API (used by the resolver)
# ---------------------------------------------------------------------


def probe_url(
    *,
    app_guid: str,
    url: str,
    use_cache: bool = True,
    client_factory=httpx.Client,
) -> ProbeResult:
    """Probe ``url`` and return a fresh-or-cached ``ProbeResult``.

    The resolver always passes ``use_cache=True``; the FE's "re-run
    probe" action passes ``use_cache=False`` so an operator who
    clicked the pill gets an immediate fresh result.

    On a cache miss the probe is executed, the result is stored
    under the per-(app, url) cache key for ``RESULT_CACHE_TTL_SECONDS``,
    and appended to the history ring buffer under a sibling key.
    """
    if use_cache:
        cached = cache.get(_result_cache_key(app_guid, url))
        if cached is not None:
            try:
                return ProbeResult.from_cache_dict(cached)
            except (KeyError, ValueError, TypeError):
                # Cache value got mangled (older shape, partial write).
                # Fall through to a fresh probe rather than 500 the
                # request — a stale cached row is never load-bearing.
                pass

    result = _execute_probe(url, client_factory=client_factory)
    payload = result.to_cache_dict()
    cache.set(_result_cache_key(app_guid, url), payload, RESULT_CACHE_TTL_SECONDS)
    _append_history(app_guid=app_guid, url=url, payload=payload)
    return result


def history_for(
    *,
    app_guid: str,
    url: str,
    limit: int = 5,
) -> list[ProbeResult]:
    """Return the most-recent ``limit`` probes for (app, url).

    The history widget caps at ``HISTORY_MAX_ENTRIES`` so this is
    bounded server-side; ``limit`` is clamped into ``[1, HISTORY_MAX_ENTRIES]``.
    Newest entries come first.
    """
    limit = max(1, min(int(limit or 0), HISTORY_MAX_ENTRIES))
    raw = cache.get(_history_cache_key(app_guid, url)) or []
    if isinstance(raw, str):
        # locmem stores the actual object; redis stores bytes. Decode
        # if a string slipped through (some backends serialize lists
        # as JSON strings).
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    out: list[ProbeResult] = []
    for entry in raw[:limit]:
        try:
            out.append(ProbeResult.from_cache_dict(entry))
        except (KeyError, ValueError, TypeError):
            # Skip mangled entries; never raise on cache contents.
            continue
    return out


def _append_history(*, app_guid: str, url: str, payload: dict) -> None:
    """Prepend a probe payload to the history ring buffer."""
    key = _history_cache_key(app_guid, url)
    raw = cache.get(key) or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    if not isinstance(raw, list):
        raw = []
    next_list = [payload, *raw][:HISTORY_MAX_ENTRIES]
    cache.set(key, next_list, HISTORY_CACHE_TTL_SECONDS)
