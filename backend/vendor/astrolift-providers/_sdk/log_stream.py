"""LogStreamDriver protocol -- stream pod/container logs from the cluster's log backend."""

from __future__ import annotations

from dataclasses import dataclass
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
