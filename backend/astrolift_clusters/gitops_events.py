"""
GitOps sync event ingestion policy (#274, spec 07 §12).

Pure-Python policy. ArgoCD's Notification controller and Flux's
Alert CRDs both POST sync events to
``/api/v1/argocd/webhook/<connection_id>`` (or the Flux equivalent).
This module owns:

* **Event-type mapping** — provider event names → platform
  event types (locked vocabulary).
* **Severity classification** — sync_failed / health_degraded
  carry warning, others informational.
* **Deployment resolution** — from the Application annotation
  ``astrolift.io/deployment-id`` to the platform Deployment row.
* **Filter rules** — drop events not destined for one of our
  Applications (operators may run mixed ArgoCD setups).

Pairs with the existing webhook-ingress HMAC verifier (#93).
"""

from __future__ import annotations

import dataclasses
from enum import Enum


class GitopsEventError(ValueError):
    pass


# ---- providers -----------------------------------------------------


class GitopsProvider(str, Enum):
    """Spec 07 §12: two reconcilers we accept events from."""

    ARGOCD = "argocd"
    FLUX = "flux"


# ---- platform event types (locked) --------------------------------


class PlatformEventType(str, Enum):
    """Spec 07 §12: one platform event per sync transition.
    Locked vocabulary — alert delivery (#159) keys off these."""

    GITOPS_SYNC_STARTED = "deployment.gitops_sync_started"
    GITOPS_SYNC_SUCCEEDED = "deployment.gitops_sync_succeeded"
    GITOPS_SYNC_FAILED = "deployment.gitops_sync_failed"
    GITOPS_HEALTH_DEGRADED = "deployment.gitops_health_degraded"


class EventSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"


# Severity per event type. Failure + health-degraded warn so the
# alert pipeline routes; sync started/succeeded are informational
# (operators can opt in to filter on them but they don't page).
_SEVERITY = {
    PlatformEventType.GITOPS_SYNC_STARTED: EventSeverity.INFO,
    PlatformEventType.GITOPS_SYNC_SUCCEEDED: EventSeverity.INFO,
    PlatformEventType.GITOPS_SYNC_FAILED: EventSeverity.WARNING,
    PlatformEventType.GITOPS_HEALTH_DEGRADED: EventSeverity.WARNING,
}


def severity_for(*, event_type: PlatformEventType) -> EventSeverity:
    if event_type not in _SEVERITY:
        raise GitopsEventError(f"unknown event type {event_type!r}")
    return _SEVERITY[event_type]


# ---- ArgoCD event mapping -----------------------------------------


# ArgoCD Notification controller emits these template names. The
# operator can rename in their setup, but the platform's
# documented webhook payload uses the canonical set below.
_ARGOCD_EVENT_MAP = {
    "on-sync-started": PlatformEventType.GITOPS_SYNC_STARTED,
    "on-sync-running": PlatformEventType.GITOPS_SYNC_STARTED,
    "on-sync-succeeded": PlatformEventType.GITOPS_SYNC_SUCCEEDED,
    "on-sync-failed": PlatformEventType.GITOPS_SYNC_FAILED,
    "on-sync-status-unknown": PlatformEventType.GITOPS_SYNC_FAILED,
    "on-health-degraded": PlatformEventType.GITOPS_HEALTH_DEGRADED,
}


# Flux Alert CRD severity + reason combinations. Flux is more
# lossless — reason carries the verb, severity carries the level.
_FLUX_REASON_MAP = {
    ("info", "Progressing"): PlatformEventType.GITOPS_SYNC_STARTED,
    ("info", "ReconciliationSucceeded"): PlatformEventType.GITOPS_SYNC_SUCCEEDED,
    ("error", "ReconciliationFailed"): PlatformEventType.GITOPS_SYNC_FAILED,
    ("error", "HealthCheckFailed"): PlatformEventType.GITOPS_HEALTH_DEGRADED,
}


def map_argocd_event(*, template_name: str) -> PlatformEventType | None:
    """Map ArgoCD Notification template name to platform event.
    Returns None for templates we don't care about so the handler
    can ack with 200 and move on."""
    return _ARGOCD_EVENT_MAP.get(template_name)


def map_flux_event(
    *, severity: str, reason: str,
) -> PlatformEventType | None:
    """Map Flux Alert (severity, reason) tuple. Returns None for
    out-of-vocabulary combinations (Flux has many reasons we don't
    care about — sourcerefnotready, etc. are part of the natural
    progression and we don't bubble those up)."""
    return _FLUX_REASON_MAP.get((severity.lower(), reason))


# ---- deployment resolution ----------------------------------------


ANNOTATION_DEPLOYMENT_ID = "astrolift.io/deployment-id"
"""Spec 07 §12: every emitted Application carries this annotation
so the GitOps event handler can resolve back to the platform
Deployment row. Manifest renderer enforces."""


@dataclasses.dataclass(frozen=True, slots=True)
class IncomingEvent:
    """Normalized input to the handler regardless of provider."""

    provider: GitopsProvider
    connection_id: int
    """Webhook URL contains the connection_id; handler-layer
    extracts and passes through."""

    event_type: PlatformEventType
    annotations: dict[str, str]
    r"""Application/Kustomization annotations. Includes our
    \`astrolift.io/deployment-id\` when emitted by us."""

    revision: str
    """Git SHA the reconciler was syncing to. Carried through
    so the event log shows what version the cluster moved to."""

    message: str = ""
    """Human-readable detail (e.g. error string for failures).
    Truncated to 1024 chars to keep events bounded."""


_MAX_MESSAGE_LENGTH = 1024


@dataclasses.dataclass(frozen=True, slots=True)
class ResolvedEvent:
    """Output of the handler — what the activity layer persists
    + emits."""

    deployment_id: int
    event_type: PlatformEventType
    severity: EventSeverity
    revision: str
    message: str
    provider: GitopsProvider


def resolve_deployment_id(*, annotations: dict[str, str]) -> int:
    """Extract + parse the platform deployment_id from
    Application annotations. Refuses missing or unparsable so
    the handler returns 400 rather than swallowing."""
    raw = annotations.get(ANNOTATION_DEPLOYMENT_ID)
    if not raw:
        raise GitopsEventError(
            f"Application missing {ANNOTATION_DEPLOYMENT_ID!r} "
            "annotation — was it emitted by Astrolift?"
        )
    try:
        deployment_id = int(raw)
    except ValueError as exc:
        raise GitopsEventError(
            f"{ANNOTATION_DEPLOYMENT_ID} annotation {raw!r} "
            "is not a valid integer"
        ) from exc
    if deployment_id <= 0:
        raise GitopsEventError(
            f"{ANNOTATION_DEPLOYMENT_ID} annotation must be > 0, "
            f"got {deployment_id}"
        )
    return deployment_id


def is_for_us(*, annotations: dict[str, str]) -> bool:
    """Pre-filter: drop events for Applications we don't own.
    Operators may run mixed ArgoCD setups (some apps managed by
    Astrolift, others not). Without this filter we'd reject
    third-party events with 400; with it we ack 200 and move on.
    """
    return ANNOTATION_DEPLOYMENT_ID in annotations


def _truncate(*, value: str) -> str:
    if len(value) <= _MAX_MESSAGE_LENGTH:
        return value
    return value[:_MAX_MESSAGE_LENGTH - 3] + "..."


def resolve(*, event: IncomingEvent) -> ResolvedEvent:
    """Validate + resolve an incoming event. Caller wraps in a
    try/except — known errors become 400, unknown become 500."""
    deployment_id = resolve_deployment_id(annotations=event.annotations)
    return ResolvedEvent(
        deployment_id=deployment_id,
        event_type=event.event_type,
        severity=severity_for(event_type=event.event_type),
        revision=event.revision,
        message=_truncate(value=event.message),
        provider=event.provider,
    )


# ---- pull-mode polling --------------------------------------------


# Pull mode is for installs that can't open inbound webhooks.
# We poll the registered cluster's ArgoCD/Flux API at this
# interval. Default 60s — fast enough that operators don't see
# stale state, slow enough to not hammer the reconciler API.
DEFAULT_PULL_INTERVAL_SECONDS = 60
MIN_PULL_INTERVAL_SECONDS = 30
MAX_PULL_INTERVAL_SECONDS = 600


def validate_pull_interval(*, seconds: int) -> int:
    """Operator-configurable; bounded so 5s polling can't DOS the
    reconciler API and 1h polling isn't useful."""
    if seconds < MIN_PULL_INTERVAL_SECONDS:
        raise GitopsEventError(
            f"pull interval {seconds}s below minimum "
            f"{MIN_PULL_INTERVAL_SECONDS}s (would hammer reconciler API)"
        )
    if seconds > MAX_PULL_INTERVAL_SECONDS:
        raise GitopsEventError(
            f"pull interval {seconds}s exceeds maximum "
            f"{MAX_PULL_INTERVAL_SECONDS}s (state too stale to be useful)"
        )
    return seconds


# ---- event deduplication ------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class EventFingerprint:
    """Used to dedupe re-deliveries. ArgoCD + Flux can both
    re-fire on transient state thrashing; same (deployment,
    event_type, revision) within a short window is a duplicate."""

    deployment_id: int
    event_type: PlatformEventType
    revision: str


def fingerprint_for(*, event: ResolvedEvent) -> EventFingerprint:
    return EventFingerprint(
        deployment_id=event.deployment_id,
        event_type=event.event_type,
        revision=event.revision,
    )


DEDUP_WINDOW_SECONDS = 60
"""Within 60s of the same fingerprint, treat as duplicate."""


def is_duplicate(
    *,
    fingerprint: EventFingerprint,
    last_seen_unix: int | None,
    now_unix: int,
) -> bool:
    """Caller looks up the most-recent (deployment_id, event_type,
    revision) seen and passes its timestamp. None = never seen
    before."""
    if last_seen_unix is None:
        return False
    return (now_unix - last_seen_unix) < DEDUP_WINDOW_SECONDS
