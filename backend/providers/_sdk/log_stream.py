"""LogStreamDriver + LogQueryDriver protocols — stream + page through
the cluster's log backend.

The streaming protocol (``LogStreamDriver``) covers live tail use
cases. The paginated query protocol (``LogQueryDriver``) covers the
historical (time-range) query the App > Observability tab uses for
"show me what happened 30 minutes ago" — bounded, cursor-paged, and
explicit about retention.

Both can be implemented by the same underlying backend (Loki,
CloudWatch, Stackdriver, Azure Monitor, ...) but the protocols are
kept separate so a backend can ship one without the other (the
kubelet stream API, for example, has no historical query surface).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass(frozen=True)
class LogLine:
    timestamp: str
    namespace: str
    pod: str
    container: str
    message: str
    level: str | None = None
    labels: dict[str, str] | None = None


class LogStreamDriver(Protocol):
    """Protocol for streaming logs from a cluster's log backend.

    Backends: Loki, CloudWatch, Stackdriver, Azure Monitor, Datadog,
    Honeycomb, New Relic, Grafana Cloud, OTLP-compatible, kubelet stream API.
    """

    def stream_logs(
        self,
        query: str,
        since: str,
        until: str,
        *,
        follow: bool = False,
    ) -> AsyncIterator[LogLine]: ...


# ---- Historical / paginated query surface --------------------------
#
# Distinct from the live-tail surface above so a backend can ship one
# without the other. The page shape is intentionally narrow — items +
# cursor + retention flag + best-effort total_count — to keep the
# wire-shape stable across Loki, CloudWatch, Stackdriver, and Azure
# Monitor (each of which exposes a different native pagination
# primitive that we hide behind ``next_cursor``).


@dataclass(frozen=True)
class LogPage:
    """One page of historical log lines from a paginated query.

    ``items`` is the page contents (oldest-first). ``next_cursor`` is
    opaque to the caller — pass it back unchanged to fetch the next
    page; empty string means end-of-window.

    ``reached_retention`` is true when the requested ``since`` predates
    the backend's configured retention — the page may still carry
    items (the visible slice) but the resolver layer badges it with
    "earlier lines were discarded by the log backend."

    ``total_count`` is best-effort — backends that can't compute it
    return -1; the resolver treats negative as "unknown" and shows the
    page-size count instead. Loki returns -1 today; CloudWatch and
    Stackdriver can hand back exact counts when their query APIs do.
    """

    items: list[LogLine] = field(default_factory=list)
    next_cursor: str = ""
    reached_retention: bool = False
    total_count: int = -1


class HistoricalLogsUnavailable(Exception):
    """Raised by a :class:`LogQueryDriver` to signal "this backend
    doesn't support historical queries" — the live-tail-only case.

    The resolver layer catches this and returns ``historical_available
    = false`` so the FE renders the "live tail only on this cluster"
    empty state. Distinct from a transport error so the operator UX
    can clearly differentiate "not implemented" from "down right now."
    """


class LogQueryDriver(Protocol):
    """Protocol for paginated historical log queries against the
    cluster's configured log aggregator.

    Backends: Loki, CloudWatch Logs Insights, Stackdriver / Google
    Cloud Logging, Azure Monitor Logs.

    Implementations should:

    * Apply the ``query`` selector verbatim (Loki LogQL stream
      selector form; per-backend translation lives on the driver).
    * Honour ``since`` / ``until`` ISO-8601 timestamps as the inclusive
      query window.
    * Cap the page at ``limit`` lines; the resolver layer clamps the
      caller's request into a safe range before reaching the driver.
    * Use ``cursor`` to resume from the previous page; treat an empty
      string as "start at the oldest line in the window."
    * Best-effort filter by ``level`` (when set, restrict to lines
      whose structured-log level matches) and ``search`` (when set,
      treat as a case-insensitive substring or, where the backend
      supports it, a regex anchored at line scope).
    * Raise :class:`HistoricalLogsUnavailable` (not return ``None``)
      when the backend has no historical surface — the resolver maps
      that to the FE's empty-state badge.
    """

    def query_logs(
        self,
        query: str,
        since: str,
        until: str,
        *,
        limit: int,
        cursor: str,
        level: str | None,
        search: str | None,
    ) -> LogPage: ...
