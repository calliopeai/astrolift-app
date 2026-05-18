"""Prometheus MetricsDriver (#4).

Default metrics backend across all clouds — boilerworks apps already
expose Prometheus exposition format, so the platform's metrics path
is "scrape and query" not "translate". The driver wraps a Prometheus
HTTP API endpoint (matrix-default Managed Prometheus on each cloud).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk.metrics import MetricDatapoint, MetricsDriver, MetricSeries


@dataclass(frozen=True)
class PrometheusConfig:
    base_url: str
    """Prometheus HTTP API root, e.g. http://prometheus:9090"""

    timeout_seconds: int = 30
    bearer_token: str | None = None
    """Optional bearer token. AWS Managed Prometheus uses SigV4
    instead — wire that via http_client injection."""

    http_client: Any | None = None


class PrometheusMetricsDriver(MetricsDriver):
    def __init__(self, *, config: PrometheusConfig) -> None:
        self._config = config
        if config.http_client is not None:
            self._http = config.http_client
        else:
            self._http = _DefaultHttp(
                timeout=config.timeout_seconds,
                bearer_token=config.bearer_token,
            )

    def query_metric(
        self,
        metric: str,
        labels: dict[str, str],
        range_start: str,
        range_end: str,
    ) -> MetricSeries:
        promql = self._build_promql(metric=metric, labels=labels)
        url = f"{self._config.base_url.rstrip('/')}/api/v1/query_range"
        params = {
            "query": promql,
            "start": range_start,
            "end": range_end,
            "step": "60s",
        }
        response = self._http.get(url, params=params)
        return self._parse_response(
            response=response, metric=metric, labels=labels,
        )

    def _build_promql(
        self, *, metric: str, labels: dict[str, str],
    ) -> str:
        if not labels:
            return metric
        bits = ",".join(
            f'{k}="{v}"' for k, v in sorted(labels.items())
        )
        return f"{metric}{{{bits}}}"

    def _parse_response(
        self,
        *,
        response: Any,
        metric: str,
        labels: dict[str, str],
    ) -> MetricSeries:
        body = response.json() if hasattr(response, "json") else response
        if body.get("status") != "success":
            raise RuntimeError(
                f"prometheus query failed: {body.get('error', body)}",
            )
        data = body.get("data", {})
        results = data.get("result", [])
        if not results:
            return MetricSeries(metric=metric, labels=labels, datapoints=[])
        # Multi-series → flatten to first; callers that need
        # multi-series should query labels explicit enough to single
        # them out. (Aggregation is the caller's job.)
        first = results[0]
        merged_labels = dict(first.get("metric", {}))
        merged_labels.pop("__name__", None)
        datapoints = [
            MetricDatapoint(timestamp=str(t), value=float(v))
            for t, v in first.get("values", [])
        ]
        return MetricSeries(
            metric=metric,
            labels=merged_labels,
            datapoints=datapoints,
        )


class _DefaultHttp:
    """Minimal stdlib-based GET client. Production wiring uses
    httpx for connection pooling + SigV4 / GCP-IAM auth."""

    def __init__(
        self, *, timeout: int = 30, bearer_token: str | None = None,
    ) -> None:
        self._timeout = timeout
        self._token = bearer_token

    def get(
        self, url: str, *, params: dict[str, str] | None = None,
    ) -> Any:
        from urllib.error import HTTPError
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen
        import json

        full = url
        if params:
            full = f"{url}?{urlencode(params)}"
        request = Request(full)
        if self._token:
            request.add_header("Authorization", f"Bearer {self._token}")
        try:
            with urlopen(request, timeout=self._timeout) as raw:
                payload = raw.read().decode("utf-8")
        except HTTPError as exc:
            raise RuntimeError(
                f"prometheus HTTP {exc.code}: {exc.reason}",
            ) from exc
        return _Response(body=payload)


@dataclass
class _Response:
    body: str

    def json(self) -> Any:
        import json

        return json.loads(self.body)
