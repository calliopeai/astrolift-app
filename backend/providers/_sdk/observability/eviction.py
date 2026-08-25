"""Observability eviction: the delete verb the SDK never had (#1602 step 3).

The four existing observability drivers -- `LokiLogStreamDriver`,
`CloudWatchLogsQueryDriver`, `PrometheusMetricsDriver`, `TempoTraceDriver`
-- are read-only. There is no delete verb anywhere in `providers/_sdk/`, so
the four `Organization` retention columns describe a promise nothing could
have kept even if something had read them.

**`supported=False` is a first-class result, not an exception.** Most
observability backends genuinely cannot delete: Prometheus evicts by its
own local retention flag, Tempo by object-store lifecycle, CloudWatch by a
log-group retention setting. A driver that raised for those would make the
sweep's error count meaningless -- every run would report failures for
backends behaving exactly as designed.

The alternative is worse and is the specific thing this shape prevents:
returning zero. A sweep that reports "0 evicted" for a backend that cannot
delete is indistinguishable from one that found nothing to delete, so the
operator reads a retention policy as enforced when nothing is enforcing it.
That false zero is the #1594 failure mode, and #1602 exists partly because
of it.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from datetime import datetime

from _sdk._telemetry import driver_op


@dataclass(frozen=True)
class EvictionRequest:
    """One window of one stream to delete.

    `selector` is backend-native (a LogQL matcher for Loki). Kept opaque
    here because the only code that can build one correctly is the driver's
    own caller, and a "portable selector" abstraction over four backends
    that mostly cannot delete would be abstraction for one implementation.
    """

    stream: str
    start: datetime
    end: datetime
    selector: str = ""
    dry_run: bool = True
    """Defaults True. A delete verb whose default is "actually delete" is
    one bad kwarg away from an unrecoverable mistake, and the sweep above
    it defaults to dry-run for the same reason."""

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("eviction window bounds must be timezone-aware")
        if self.end <= self.start:
            # Not pedantry: an inverted or empty window sent to Loki is
            # accepted and deletes nothing, so the sweep would count a
            # successful eviction that removed no data.
            raise ValueError(f"eviction window must be non-empty: {self.start} .. {self.end}")


@dataclass(frozen=True)
class EvictionOutcome:
    """What a backend did, or why it could not."""

    supported: bool
    requested: int = 0
    """Windows the driver asked the backend to delete. Deliberately named
    `requested` and not `deleted`: Loki's delete API is asynchronous -- the
    compactor applies it later -- so claiming rows were deleted at return
    time would be a lie the API cannot back up."""
    detail: str = ""
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.supported and self.requested:
            raise ValueError("an unsupported backend cannot have requested an eviction")


class ObservabilityEvictionDriver(Protocol):
    def evict_before(self, request: EvictionRequest) -> EvictionOutcome: ...


class UnsupportedEvictionDriver:
    """Returned for every backend with no delete API.

    Carries the backend's name so the sweep can report *which* backend was
    skipped and why, rather than an anonymous count. An operator reading
    "skipped 3" learns nothing; "skipped: tempo has no delete API, evict via
    object-store lifecycle" tells them where retention actually lives.
    """

    def __init__(self, *, backend: str, hint: str = "") -> None:
        self._backend = backend
        self._hint = hint

    def evict_before(self, request: EvictionRequest) -> EvictionOutcome:
        detail = f"{self._backend} has no delete API"
        if self._hint:
            detail = f"{detail}; {self._hint}"
        return EvictionOutcome(supported=False, requested=0, detail=detail)


# Where retention actually lives for each backend that cannot delete. The
# hints exist so a skipped stream points somewhere, rather than reading as
# a gap in the platform.
UNSUPPORTED_BACKENDS: dict[str, str] = {
    "prometheus": "set --storage.tsdb.retention.time on the Prometheus server",
    "managed_prometheus": "retention is a workspace setting on the managed service",
    "tempo": "evict via the trace object store's lifecycle policy",
    "cloudwatch_logs": "set the log group's retentionInDays",
}


def unsupported_driver_for(backend: str) -> UnsupportedEvictionDriver:
    return UnsupportedEvictionDriver(
        backend=backend,
        hint=UNSUPPORTED_BACKENDS.get(backend, ""),
    )


class LokiEvictionDriver:
    """Loki's compactor delete API -- the one real implementation here.

    Reuses `LokiConfig` rather than defining a second config for the same
    endpoint, so a cluster cannot end up querying one Loki and deleting from
    another.

    Deletion is off by default in Loki: it needs
    `-compactor.retention-enabled` and a delete mode. When it is off the API
    refuses, and that refusal is reported as `supported=False` rather than
    an error, because a Loki without deletion enabled is a configuration
    choice, not a fault.
    """

    def __init__(self, *, config: Any) -> None:
        self._config = config
        self._http = getattr(config, "http_client", None) or _DeleteHttp(
            timeout=getattr(config, "timeout_seconds", 30),
            bearer_token=getattr(config, "bearer_token", None),
            org_id=getattr(config, "org_id", None),
        )

    @driver_op(driver="loki_eviction")
    def evict_before(self, request: EvictionRequest) -> EvictionOutcome:
        selector = request.selector or '{job=~".+"}'
        if request.dry_run:
            # Returns before any call. Loki has no dry-run parameter, so the
            # only honest dry run is not issuing the request -- asking the
            # compactor "what would you delete" is not something the API
            # offers, and issuing a real delete with a flag set would be the
            # opposite of a dry run.
            return EvictionOutcome(
                supported=True,
                requested=0,
                detail=f"dry run: would request delete of {selector} over {request.start}..{request.end}",
                labels={"selector": selector},
            )

        url = f"{str(getattr(self._config, 'base_url', '')).rstrip('/')}/loki/api/v1/delete"
        params = {
            "query": selector,
            # Loki wants unix seconds on this endpoint.
            "start": str(int(request.start.timestamp())),
            "end": str(int(request.end.timestamp())),
        }
        try:
            status = self._http.post(url, params=params)
        except Exception as exc:
            message = str(exc)
            if _looks_like_deletion_disabled(message):
                return EvictionOutcome(
                    supported=False,
                    requested=0,
                    detail=f"loki deletion is not enabled: {message}",
                )
            raise

        return EvictionOutcome(
            supported=True,
            requested=1,
            detail=(f"loki accepted delete request (status {status}); the compactor applies it asynchronously"),
            labels={"selector": selector},
        )


def _looks_like_deletion_disabled(message: str) -> bool:
    """Whether Loki's refusal means "deletion is off" rather than "failed".

    Matched on the message because Loki answers with a 400 either way, so
    the status code cannot separate a misconfigured platform from one where
    an operator deliberately left deletion disabled. Erring toward
    `supported=False` here is the safer direction: it reports a skip an
    operator can investigate, where erring the other way raises on a
    correctly configured install every 24 hours.
    """
    lowered = message.lower()
    return any(
        phrase in lowered
        for phrase in (
            "deletion is not available",
            "delete requests are not enabled",
            "not enabled",
            "disabled",
        )
    )


class _DeleteHttp:
    """POST with optional auth + org-id header.

    Separate from `loki_logs._DefaultHttp`, which only implements `get`.
    Not extended there: adding a `post` to the read path's client would put
    a delete-capable method on the object every log query holds.
    """

    def __init__(self, *, timeout: int = 30, bearer_token: str | None = None, org_id: str | None = None) -> None:
        self._timeout = timeout
        self._token = bearer_token
        self._org_id = org_id

    def post(self, url: str, *, params: dict[str, str] | None = None) -> int:
        from urllib.error import HTTPError
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen

        full = f"{url}?{urlencode(params)}" if params else url
        request = Request(full, method="POST", data=b"")
        if self._token:
            request.add_header("Authorization", f"Bearer {self._token}")
        if self._org_id:
            request.add_header("X-Scope-OrgID", self._org_id)
        try:
            with urlopen(request, timeout=self._timeout) as response:
                return int(response.status)
        except HTTPError as exc:
            # The body carries Loki's reason, and the reason is what
            # separates "deletion is disabled" from a real failure -- so it
            # is worth reading, and worth not failing over if it cannot be.
            body = ""
            with contextlib.suppress(Exception):
                body = exc.read().decode("utf-8", "replace")[:400]
            raise RuntimeError(f"loki delete failed ({exc.code}): {body}") from exc
