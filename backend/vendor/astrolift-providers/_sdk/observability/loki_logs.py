"""Loki LogStreamDriver (#2).

Loki is the matrix-default log backend across all clouds — boilerworks
apps emit JSON-line logs, the cluster's promtail / vector / Loki
write-side ships them, and the driver queries via Loki's logcli HTTP
API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from _sdk.log_stream import LogLine, LogPage, LogStreamDriver

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass(frozen=True)
class LokiConfig:
    base_url: str
    """Loki HTTP API root, e.g. http://loki:3100"""

    timeout_seconds: int = 30
    bearer_token: str | None = None
    org_id: str | None = None
    """X-Scope-OrgID header for multi-tenant Loki / Grafana Cloud."""

    http_client: Any | None = None
    page_size: int = 1000


class LokiLogStreamDriver(LogStreamDriver):
    def __init__(self, *, config: LokiConfig) -> None:
        self._config = config
        if config.http_client is not None:
            self._http = config.http_client
        else:
            self._http = _DefaultHttp(
                timeout=config.timeout_seconds,
                bearer_token=config.bearer_token,
                org_id=config.org_id,
            )

    async def stream_logs(
        self,
        query: str,
        since: str,
        until: str,
        *,
        follow: bool = False,
    ) -> AsyncIterator[LogLine]:
        url = f"{self._config.base_url.rstrip('/')}/loki/api/v1/query_range"
        params = {
            "query": query,
            "start": since,
            "end": until,
            "limit": str(self._config.page_size),
            "direction": "forward",
        }
        response = self._http.get(url, params=params)
        body = response.json() if hasattr(response, "json") else response
        if body.get("status") != "success":
            raise RuntimeError(
                f"loki query failed: {body.get('error', body)}",
            )
        results = body.get("data", {}).get("result", [])
        for stream in results:
            stream_labels = dict(stream.get("stream", {}))
            namespace = stream_labels.get("namespace", "")
            pod = stream_labels.get("pod", "") or stream_labels.get(
                "kubernetes_pod_name", "",
            )
            container = (
                stream_labels.get("container", "")
                or stream_labels.get("kubernetes_container_name", "")
            )
            for ts_ns, message in stream.get("values", []):
                yield LogLine(
                    timestamp=str(ts_ns),
                    namespace=namespace,
                    pod=pod,
                    container=container,
                    message=message,
                    level=stream_labels.get("level"),
                    labels=stream_labels,
                )

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
        """Loki historical query (#482).

        Cursor is the nanosecond timestamp of the last item from the
        previous page. Loki's ``query_range`` is half-open at the lower
        bound; we add 1ns to the cursor so we don't double-emit.

        ``level`` is appended to the LogQL stream selector when set so
        the filter runs server-side. ``search`` becomes a ``|=`` LogQL
        line filter for the same reason — both extras avoid pulling the
        whole window across the wire just to discard rows.
        """
        url = f"{self._config.base_url.rstrip('/')}/loki/api/v1/query_range"
        start = cursor if cursor else since
        # The selector is the caller's responsibility; we layer level +
        # search on top as LogQL pipeline steps.
        full_query = query
        if level:
            # LogQL doesn't allow arbitrary label injection in the
            # selector — append as a label_format filter via JSON parse
            # if the operator uses structured logs; for plain text we
            # fall back to a line filter for the level token.
            full_query = f'{full_query} | json | level=~"(?i){_loki_quote(level)}"'
        if search:
            full_query = f'{full_query} |= "{_loki_quote(search)}"'
        params = {
            "query": full_query,
            "start": start,
            "end": until,
            "limit": str(min(limit, self._config.page_size)),
            "direction": "forward",
        }
        response = self._http.get(url, params=params)
        body = response.json() if hasattr(response, "json") else response
        if body.get("status") != "success":
            raise RuntimeError(
                f"loki query failed: {body.get('error', body)}",
            )
        results = body.get("data", {}).get("result", [])
        items: list[LogLine] = []
        last_ns = ""
        for stream in results:
            stream_labels = dict(stream.get("stream", {}))
            namespace = stream_labels.get("namespace", "")
            pod = stream_labels.get("pod", "") or stream_labels.get(
                "kubernetes_pod_name", "",
            )
            container = (
                stream_labels.get("container", "")
                or stream_labels.get("kubernetes_container_name", "")
            )
            for ts_ns, message in stream.get("values", []):
                items.append(
                    LogLine(
                        timestamp=str(ts_ns),
                        namespace=namespace,
                        pod=pod,
                        container=container,
                        message=message,
                        level=stream_labels.get("level"),
                        labels=stream_labels,
                    )
                )
                last_ns = str(ts_ns)
        # Sort by timestamp so multi-stream results merge cleanly; Loki
        # returns one block per labelset which interleaves chronologically
        # only within the block.
        items.sort(key=lambda x: x.timestamp)
        # Page is full -> caller has more to fetch; emit the cursor one
        # ns past the tail to skip the boundary line on the next round.
        next_cursor = ""
        if len(items) >= limit and last_ns:
            try:
                next_cursor = str(int(last_ns) + 1)
            except ValueError:
                next_cursor = ""
        return LogPage(
            items=items,
            next_cursor=next_cursor,
            reached_retention=False,
        )


def _loki_quote(value: str) -> str:
    """Escape backslash + double-quote so the operator-supplied filter
    can't break out of the LogQL string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


class _DefaultHttp:
    """Stdlib GET with optional auth + org-id header."""

    def __init__(
        self,
        *,
        timeout: int = 30,
        bearer_token: str | None = None,
        org_id: str | None = None,
    ) -> None:
        self._timeout = timeout
        self._token = bearer_token
        self._org_id = org_id

    def get(
        self, url: str, *, params: dict[str, str] | None = None,
    ) -> Any:
        from urllib.error import HTTPError
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen

        full = url
        if params:
            full = f"{url}?{urlencode(params)}"
        request = Request(full)
        if self._token:
            request.add_header("Authorization", f"Bearer {self._token}")
        if self._org_id:
            request.add_header("X-Scope-OrgID", self._org_id)
        try:
            with urlopen(request, timeout=self._timeout) as raw:
                payload = raw.read().decode("utf-8")
        except HTTPError as exc:
            raise RuntimeError(
                f"loki HTTP {exc.code}: {exc.reason}",
            ) from exc
        return _Response(body=payload)


@dataclass
class _Response:
    body: str

    def json(self) -> Any:
        import json

        return json.loads(self.body)
