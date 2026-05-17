"""LogStreamDriver protocol -- stream pod/container logs from the cluster's log backend.

Two surfaces live here:

* :class:`LogStreamDriver` — the original push surface (``stream_logs``),
  used by the on-app-log subscription. Backends emit ``LogLine`` records
  as they arrive; the resolver yields them onward into the WebSocket.

* :meth:`LogStreamDriver.query_logs` (#482) — paginated, time-range
  history. Drives the "show me all error lines from the last 30 minutes"
  surface. Returns a :class:`LogPage` (items + nextCursor +
  reachedRetention) so the FE doesn't have to invent cursor framing.

Backends that already paginate server-side (Loki, CloudWatch,
Stackdriver) implement ``query_logs`` directly. Backends without a
native paginated history (the kubelet stream API in particular) can
either raise :class:`HistoricalLogsUnavailable` or use
:func:`drain_stream_as_page` to materialize a page off ``stream_logs``
— the helper exists for tests + bare-metal clusters where Loki isn't
installed.
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


@dataclass(frozen=True)
class LogPage:
    """One page of historical log lines (#482).

    ``items`` is the page contents, ordered oldest-first. ``next_cursor``
    is opaque to the caller — pass it back unchanged to fetch the next
    page. Empty string means "no more pages in this window".

    ``reached_retention`` is true when the requested ``since`` predates
    the backend's configured retention window. Pages may still contain
    items (the visible slice of the window); the flag tells the FE to
    badge the result with "older than retention — earlier lines were
    discarded by the log backend."
    """

    items: list[LogLine] = field(default_factory=list)
    next_cursor: str = ""
    reached_retention: bool = False


class HistoricalLogsUnavailable(RuntimeError):  # noqa: N818 — matches issue #482's wire-side error code so the surface name is the same in code, error tag, and FE warning copy
    """Raised by ``query_logs`` when the backend has no historical
    surface (kubelet stream-only, no Loki configured, etc.). The
    resolver layer maps this to ``historicalAvailable: false`` on the
    GraphQL page so the FE can fall back to live-tail-only mode.
    """


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

    def query_logs(
        self,
        query: str,
        since: str,
        until: str,
        *,
        limit: int = 500,
        cursor: str = "",
        level: str | None = None,
        search: str | None = None,
    ) -> LogPage:
        """Paginated historical query over ``[since, until]`` (#482).

        ``query`` is the backend-specific selector (LogQL stream
        selector for Loki, log group name for CloudWatch, filter
        expression for Stackdriver).

        ``cursor`` is opaque — empty for the first page, the value
        returned in ``LogPage.next_cursor`` for subsequent pages. The
        empty string in the returned ``next_cursor`` signals end-of-
        window.

        ``level`` and ``search`` are server-side filters: backends
        that can't push the filter down do it client-side after the
        page is drained. Both are optional — the resolver only passes
        them through when the operator explicitly filters.

        Backends with no historical surface raise
        :class:`HistoricalLogsUnavailable` so the resolver can surface
        ``historicalAvailable=false`` on the GraphQL page.
        """
        ...
