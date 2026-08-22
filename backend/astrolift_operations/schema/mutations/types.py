"""Strawberry input and payload types for the mutation package."""

from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import GUID
from astrolift_operations.schema.types import (
    WebhookSubscriptionType,
)


@strawberry.input
class TestWebhookInput:
    """Trigger a synchronous test delivery against an existing
    subscription. The subscription must be active to test; disabled
    rows refuse."""

    id: GUID


@strawberry.input
class TestNotificationInput:
    """Send a test notification to the caller's inbox, scoped to the
    org identified by ``id``. The caller must be authenticated and
    a member of that org (operators only, via ``org.update``).

    ``message`` is optional; if absent a stock 'this is a test
    notification' body is used."""

    id: GUID
    """The organization guid the notification is scoped to."""

    message: str | None = None


@strawberry.input
class CreateWebhookSubscriptionInput:
    url: str
    events: list[str]
    team_slug: str | None = None
    app_slug: str | None = None
    """When set, scopes the subscription to a single app (#281).
    Empty / null = org-wide subscription, same as before."""

    format: str | None = None
    """Outbound payload shape: ``generic`` (default) | ``slack`` | ``discord``."""


@strawberry.input
class UpdateWebhookSubscriptionInput:
    id: GUID
    url: str | None = None
    events: list[str] | None = None
    is_active: bool | None = None
    format: str | None = None
    """Outbound payload shape: ``generic`` | ``slack`` | ``discord``."""

    # Optimistic-concurrency gate (#497) — null skips the check.
    if_match_version: int | None = None


@strawberry.input
class RotateOutboundWebhookSecretInput:
    """Rotate the HMAC secret for an outbound webhook subscription
    (#426). The previous secret stays valid for the Constance
    ``WEBHOOK_SECRET_ROTATION_GRACE_SECONDS`` window so subscribers
    can roll out the new value without dropping deliveries."""

    id: GUID


@strawberry.input
class DeleteWebhookSubscriptionInput:
    id: GUID


@strawberry.input
class MarkNotificationReadInput:
    id: GUID


@strawberry.type
class _MarkAllReadPayload:
    marked: int


@strawberry.input
class RegisterMobileDeviceInput:
    device_token: str
    platform: str
    """ios | android | web_push"""

    label: str | None = None


@strawberry.input
class RevokeMobileDeviceInput:
    id: GUID


@strawberry.type
class _RevokeMobileDevicePayload:
    id: GUID
    revoked: bool


@strawberry.input
class SetNotificationPreferenceInput:
    channel: str
    """push | email | webhook (only ``push`` is wired today)"""

    event_kind: str
    """One of ``iter_template_event_types()`` (or a
    ``auth.session.created.<client_kind>`` sub-key). Unknown kinds
    are accepted — the dispatcher just won't fire on them until a
    template lands."""

    enabled: bool


@strawberry.input
class SetNotificationProfileInput:
    """Install the notification driver for the caller's org (#490).

    Upsert, not append: the DB allows one active profile per org, so
    re-submitting rewrites the live row's driver/config in place.
    ``config`` is the per-driver blob whose required keys the policy
    module enforces (region + platform_applications for ``aws_sns``,
    project_id for ``gcp_fcm``, primary + secondaries for
    ``multiplexer``, and so on)."""

    driver: str
    """``aws_sns`` | ``gcp_fcm`` | ``azure_anh`` | ``otlp_webhook``
    | ``multiplexer``."""

    config: strawberry.scalars.JSON

    retention_delivery_days: int | None = None
    """Days of NotificationDelivery audit rows to keep. Omit for the
    30-day default; 365 is the ceiling."""


@strawberry.input
class CreateAlertRuleInput:
    name: str
    target: str
    """app | env | workload | global"""

    target_id: str | None = None
    severity: str | None = None
    """info | warn | critical (default: warn)"""

    predicate: strawberry.scalars.JSON | None = None
    notify_channels: strawberry.scalars.JSON | None = None
    is_active: bool | None = None
    managed_service_id: GUID | None = None
    """Optional binding to a ManagedService instance. Required for
    per-service predicate kinds like ``ses_bounce_rate`` and
    ``ses_complaint_rate`` so the evaluator can resolve the live
    driver. Ignored for global / PromQL rules."""


@strawberry.input
class UpdateAlertRuleInput:
    id: GUID
    name: str | None = None
    severity: str | None = None
    predicate: strawberry.scalars.JSON | None = None
    notify_channels: strawberry.scalars.JSON | None = None
    is_active: bool | None = None
    managed_service_id: GUID | None = None
    """Re-point the bound managed service. Pass ``null`` from the
    client to leave the binding unchanged. The current shape doesn't
    support *unbinding* — operators delete + recreate to remove a
    binding (cheap; rules don't carry history)."""


@strawberry.input
class DeleteAlertRuleInput:
    id: GUID


@strawberry.input
class AcknowledgeAlertEventInput:
    id: GUID


@strawberry.input
class MuteAlertRuleInput:
    """Silence an alert rule for ``durationSeconds`` (#434 scope C).

    Mute carries a TTL so it auto-expires; no human has to remember
    to unmute. ``reason`` is required because post-incident review
    needs the answer to "why was this silenced?". Re-muting an
    already-muted rule is allowed and extends the silence — the
    longest-lived mute wins."""

    rule_id: GUID
    duration_seconds: int
    """Mute TTL in seconds. Capped at 7 days (604800s) so a
    forgotten mute doesn't silently outlive the team's interest."""

    reason: str


@strawberry.input
class UnmuteAlertRuleInput:
    """Immediate unmute — soft-deletes every active mute on the rule
    so the next firing fans out to channels. The mute history rows
    stay around for the audit log; only the *active* mute is cleared."""

    rule_id: GUID


@strawberry.input
class ExportAuditEventsInput:
    """Filter snapshot for the audit-log export (#433).

    Bounds + filters must match the active UI query so what the
    operator sees on screen is what lands in the file. Cap on row
    count enforced server-side via the ``AUDIT_EXPORT_MAX_ROWS``
    Constance flag — out-of-bound exports fail loudly rather than
    truncating silently."""

    format: str
    """``CSV`` or ``NDJSON``. Case-insensitive."""

    created_at_gte: dt.datetime | None = None
    created_at_lte: dt.datetime | None = None
    action: str | None = None
    decision: str | None = None
    actor_id: str | None = None


@strawberry.input
class ExportAppLogsInput:
    """Filter snapshot for the app-log export (#483).

    The mutation streams runtime container log lines from the
    cluster's log backend, applies the level + regex filters in
    process, and writes the bytes to a token-gated download URL.
    Cap on line count enforced via ``APP_LOG_EXPORT_MAX_LINES``
    Constance flag — out-of-bound exports return ``truncated=true``
    so the operator can narrow filters and retry.

    Mobile-friendly: every field except ``app_slug`` + ``format`` is
    optional, so a mobile client can fire a one-shot 'last hour, raw
    text' export with two fields."""

    app_slug: str
    format: str
    """``CSV`` | ``NDJSON`` | ``TXT``. Case-insensitive."""

    environment_name: str | None = None
    """Scope to a single env's cluster. When null, the resolver picks
    the app's default cluster."""

    workload_slug: str | None = None
    """Reserved for future per-workload streaming via the log
    backend. Recorded on the export row for the audit trail; current
    serializer ignores it because the cluster driver streams at the
    pod granularity."""

    pod_name: str | None = None
    """Single-pod scope. When null, the mutation refuses — the
    cluster driver's ``stream_logs`` requires a pod name; multi-pod
    aggregation is a future enhancement (#483 follow-up)."""

    container: str | None = None
    """Single container within ``pod_name``. When null, the cluster
    driver picks the default container."""

    since: dt.datetime | None = None
    until: dt.datetime | None = None
    """ISO timestamps bounding the requested window. Both optional;
    the cluster driver applies its own ``tail_lines`` cap regardless."""

    level: str | None = None
    """Case-insensitive substring match against the log message.
    ``"ERROR"`` matches lines containing ``error`` or ``ERROR``."""

    regex: str | None = None
    """Python regex over the message. Invalid patterns return
    VALIDATION failure with ``field='regex'``."""


@strawberry.input
class SetAlertSubscriptionInput:
    app_slug: str
    alert_kind: str
    """One of: deploy_success / deploy_failure / error_spike /
    email_bounce_threshold / preview_created / preview_destroyed /
    cert_renewal_failed"""

    channel: str
    """email | web | both"""

    enabled: bool


@strawberry.input
class ClearAlertSubscriptionInput:
    id: GUID


@strawberry.type
class _AlertRuleDeletedPayload:
    id: GUID
    deleted: bool


@strawberry.type
class _SoftDeletePayload:
    id: GUID
    deleted: bool


@strawberry.type
class WebhookSecretReveal:
    """Returned exactly once on creation — the HMAC secret never lives plaintext in DB."""

    subscription: WebhookSubscriptionType
    plaintext_secret: str


@strawberry.type
class BulkAppResultItem:
    """Per-app outcome row inside a ``BulkOperationResult`` (#746)."""

    app_slug: str
    ok: bool
    errors: list[str]


@strawberry.type
class BulkOperationResult:
    """Fan-out result envelope for bulk app operations (#746).

    ``ok_count`` and ``failed_count`` give quick totals for toast
    rendering; ``per_app`` carries the per-row breakdown so the FE can
    annotate the list items that failed."""

    ok_count: int
    failed_count: int
    per_app: list[BulkAppResultItem]


@strawberry.input
class BulkRollingRestartInput:
    app_slugs: list[str]
    environment_name: str | None = None
    """When set, scope the restart to workloads on that environment's
    cluster only. When null, restarts across all environments."""


@strawberry.input
class BulkPushSecretsInput:
    app_slugs: list[str]
    bundle_slug: str
    environment_name: str | None = None


@strawberry.input
class BulkResyncManifestInput:
    app_slugs: list[str]
