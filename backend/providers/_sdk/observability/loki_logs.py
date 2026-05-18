"""Loki LogStreamDriver (#2).

Loki is the matrix-default log backend across all clouds — boilerworks
apps emit JSON-line logs, the cluster's promtail / vector / Loki
write-side ships them, and the driver queries via Loki's logcli HTTP
API.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.log_stream import LogLine, LogStreamDriver


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

    @driver_op(driver="loki_logs")
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
                "kubernetes_pod_name",
                "",
            )
            container = stream_labels.get("container", "") or stream_labels.get("kubernetes_container_name", "")
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
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
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
