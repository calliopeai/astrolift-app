"""Mutations for the operations app: webhook CRUD + notification mark-read."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import secrets
import time
import urllib.error
import urllib.request
from datetime import UTC

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Organization, Team
from astrolift_operations.models import (
    AlertEvent,
    AlertMute,
    AlertRule,
    AuditEvent,
    AuditExport,
    Notification,
    UserAlertSubscription,
    WebhookSubscription,
)
from astrolift_operations.schema.types import (
    AlertEventType,
    AlertRuleType,
    AppLogExportType,
    AuditExportType,
    DeviceRegistrationType,
    NotificationPreferenceType,
    NotificationType,
    UserAlertSubscriptionType,
    WebhookSubscriptionType,
    WebhookTestResultType,
    alert_event_to_type,
    alert_rule_to_type,
    app_log_export_to_type,
    audit_export_to_type,
    notification_to_type,
    user_alert_subscription_to_type,
    webhook_to_type,
)
from astrolift_operations.webhook_delivery import build_headers, sign_payload
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.optimistic import check_version_match as _check_version_match
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

log = logging.getLogger(__name__)


def _caller_org_id() -> int | None:
    """Current tenant's organization id, or None when there's no tenant
    context. Mutations over org-owned rows MUST treat None as
    deny-by-default (not-found), never as "all rows" (#1042 / #1183).

    ``@tenant_scoped()`` only asserts a tenant context exists; it does
    NOT filter any queryset. Every mutation that fetches by slug or guid
    has to add the org constraint itself or it reads/writes cross-org.
    """
    tenant = get_current_tenant()
    return tenant.organization_id if tenant is not None else None


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


# ---- Push device registration (#476 §B) --------------------------


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


# ---- Notification preferences (#476 §F, #499 §E) -----------------


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


# Alert rules + events (#282) ---------------------------------------


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


# Alert mute (#434 scope C) ----------------------------------------


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


# Alert subscriptions (#747) -----------------------------------------


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


# ---- Bulk ops (#746) ----------------------------------------------


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


_BULK_APP_CAP = 20


# ---- Test-delivery helper ------------------------------------------


_WEBHOOK_TEST_TIMEOUT_SECONDS = 10


def _deliver_test_webhook(
    *,
    url: str,
    secret: bytes,
    payload: dict,
    event_type: str,
    format: str = "generic",
) -> dict:
    """POST ``payload`` to ``url`` with the standard webhook headers
    + HMAC signature. Returns a result dict the caller folds into
    :class:`WebhookTestResultType`.

    Synchronous on purpose: the real DeliverWebhookWorkflow handles
    retries + backoff, but a manual test wants the immediate verdict
    so the operator can wire the integration without leaving the UI.

    ``format`` selects the outbound shape: subscribers wired to
    Slack / Discord get a vendor-shaped body so the test message
    renders correctly in their channel.
    """
    from astrolift_operations.webhook_format import adapt_payload

    shaped = adapt_payload(format=format, envelope=payload)
    raw_body = json.dumps(shaped, separators=(",", ":")).encode("utf-8")
    timestamp_unix = int(time.time())
    signature = sign_payload(
        secret=secret,
        timestamp_unix=timestamp_unix,
        raw_body=raw_body,
    )
    delivery_id = secrets.token_urlsafe(16)
    headers = build_headers(
        event_type=event_type,
        event_id=delivery_id,
        delivery_id=delivery_id,
        schema_version="1",
        signature=signature,
        timestamp_unix=timestamp_unix,
    )

    req = urllib.request.Request(  # noqa: S310 — url validated by URLField on save
        url,
        data=raw_body,
        method="POST",
        headers=headers,
    )

    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=_WEBHOOK_TEST_TIMEOUT_SECONDS) as resp:  # noqa: S310
            body = resp.read(2048)
            duration_ms = int((time.monotonic() - started) * 1000)
            return {
                "delivered": True,
                "status_code": resp.status,
                "duration_ms": duration_ms,
                "response_body_excerpt": body.decode("utf-8", errors="replace")[:512],
                "error": "",
                "delivery_id": delivery_id,
                "timestamp_unix": timestamp_unix,
            }
    except urllib.error.HTTPError as exc:
        # Got a response but non-2xx → record it as a delivered failure
        # so operators can see the 4xx/5xx + body.
        body = b""
        try:
            body = exc.read(2048) or b""
        except Exception:  # noqa: BLE001 — best-effort body read
            body = b""
        duration_ms = int((time.monotonic() - started) * 1000)
        return {
            "delivered": True,
            "status_code": exc.code,
            "duration_ms": duration_ms,
            "response_body_excerpt": body.decode("utf-8", errors="replace")[:512],
            "error": "",
            "delivery_id": delivery_id,
            "timestamp_unix": timestamp_unix,
        }
    except Exception as exc:  # noqa: BLE001 — every transport error is a delivery miss
        duration_ms = int((time.monotonic() - started) * 1000)
        return {
            "delivered": False,
            "status_code": None,
            "duration_ms": duration_ms,
            "response_body_excerpt": "",
            "error": str(exc)[:512],
            "delivery_id": delivery_id,
            "timestamp_unix": timestamp_unix,
        }


@strawberry.type
class OperationsMutation:
    @strawberry.field
    @mutation_audit(action="webhook.create")
    @require_permission(Permission.WEBHOOK_CREATE)
    @tenant_scoped()
    def create_webhook_subscription(
        self, info: Info, input: CreateWebhookSubscriptionInput
    ) -> MutationResultType[WebhookSecretReveal]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        team = None
        if input.team_slug:
            team = Team.objects.filter(organization=org, slug=input.team_slug).first()
            if team is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "team not found", field="teamSlug")

        registered_app = None
        if input.app_slug:
            from astrolift_registry.models import RegisteredApp

            registered_app = RegisteredApp.objects.filter(organization=org, slug=input.app_slug).first()
            if registered_app is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    f"app {input.app_slug!r} not found",
                    field="appSlug",
                )

        plaintext_secret = "alfthk_" + secrets.token_urlsafe(24)
        digest = hashlib.sha256(plaintext_secret.encode()).hexdigest()

        fmt = (input.format or WebhookSubscription.Format.GENERIC).lower()
        valid_formats = {c for c, _ in WebhookSubscription.Format.choices}
        if fmt not in valid_formats:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"format must be one of {sorted(valid_formats)}",
                field="format",
            )

        sub = WebhookSubscription.objects.create(
            organization=org,
            team=team,
            registered_app=registered_app,
            url=input.url.strip(),
            secret_hash=digest,
            events=list(input.events or []),
            is_active=True,
            format=fmt,
        )
        return gql_success(
            WebhookSecretReveal(
                subscription=webhook_to_type(sub),
                plaintext_secret=plaintext_secret,
            )
        )

    @strawberry.field
    @mutation_audit(action="webhook.update")
    @require_permission(Permission.WEBHOOK_UPDATE)
    @tenant_scoped()
    def update_webhook_subscription(
        self, info: Info, input: UpdateWebhookSubscriptionInput
    ) -> MutationResultType[WebhookSubscriptionType]:
        # Scope to the caller's org (#1183): a bare guid lookup let any
        # tenant edit another tenant's webhook (retarget the URL, flip
        # active). org_id None → deny-by-default (not-found).
        sub = WebhookSubscription.objects.filter(guid=str(input.id), organization_id=_caller_org_id()).first()
        if sub is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "subscription not found")
        # #497 — optimistic-concurrency gate.
        mismatch = _check_version_match(
            sub, if_match_version=input.if_match_version, kind="WebhookSubscription"
        )
        if mismatch is not None:
            return mismatch
        if input.url is not None:
            sub.url = input.url
        if input.events is not None:
            sub.events = list(input.events)
        if input.is_active is not None:
            sub.is_active = input.is_active
            # Operator re-enable also clears the auto-disable bookkeeping
            # so the next failure doesn't immediately re-trip the
            # threshold from a stale counter. Manual disable leaves the
            # counter alone — operators see history when they look.
            if input.is_active:
                sub.disabled_at = None
                sub.disabled_reason = ""
                sub.failure_count = 0
            else:
                if not sub.disabled_at:
                    from django.utils import timezone

                    sub.disabled_at = timezone.now()
                    sub.disabled_reason = "operator-disabled"
        if input.format is not None:
            fmt = input.format.lower()
            valid_formats = {c for c, _ in WebhookSubscription.Format.choices}
            if fmt not in valid_formats:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"format must be one of {sorted(valid_formats)}",
                    field="format",
                )
            sub.format = fmt
        sub.save()
        return gql_success(webhook_to_type(sub))

    @strawberry.field
    @mutation_audit(action="webhook.delete")
    @require_permission(Permission.WEBHOOK_DELETE)
    @tenant_scoped()
    def delete_webhook_subscription(
        self, info: Info, input: DeleteWebhookSubscriptionInput
    ) -> MutationResultType[_SoftDeletePayload]:
        # Scope to the caller's org (#1183): without it any tenant could
        # soft-delete another tenant's webhook by guid. org_id None →
        # deny-by-default (not-found).
        sub = WebhookSubscription.objects.filter(guid=str(input.id), organization_id=_caller_org_id()).first()
        if sub is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "subscription not found")
        sub.soft_delete()
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    @strawberry.field
    @mutation_audit(action="webhook.test")
    @require_permission(Permission.WEBHOOK_UPDATE)
    @tenant_scoped()
    def test_webhook_subscription(
        self, info: Info, input: TestWebhookInput
    ) -> MutationResultType[WebhookTestResultType]:
        """Synthesize a ``webhook.test`` event, sign it with the
        subscription's secret derivation key, and POST it
        synchronously so the operator gets an immediate
        verdict (status + latency + body excerpt).

        Distinct from the async DeliverWebhookWorkflow: no retries,
        no auto-disable bookkeeping — this is a one-shot probe, not
        traffic. The subscription's ``failure_count`` is left alone.
        """
        from datetime import datetime

        # Scope to the caller's org (#1183): a test-fire POSTs a signed
        # payload to the subscription's URL, so a cross-org guid would
        # let a tenant probe another tenant's endpoint. org_id None →
        # deny-by-default (not-found).
        sub = WebhookSubscription.objects.filter(
            guid=str(input.id), organization_id=_caller_org_id(), deleted_at__isnull=True
        ).first()
        if sub is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "subscription not found", field="id")
        if not sub.is_active:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "subscription is disabled; re-enable before testing",
                field="id",
            )
        if not sub.url:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "subscription has no url configured",
                field="id",
            )

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        payload = {
            "kind": "webhook.test",
            "subscription_id": str(sub.guid),
            "organization_id": org_id,
            "delivered_at": datetime.now(tz=UTC).isoformat(),
            "message": "test delivery from the Astrolift control plane",
        }

        outcome = _deliver_test_webhook(
            url=sub.url,
            secret=(sub.secret_hash or "").encode("utf-8"),
            payload=payload,
            event_type="webhook.test",
            format=sub.format or "generic",
        )

        # Persist a WebhookDelivery row so the test-fire shows up in
        # the operator UI's history table. Test rows carry is_test=True
        # so health widgets can exclude probe traffic. We bypass
        # record_delivery_outcome on purpose — tests must not touch
        # the subscription's failure_count or auto-disable bookkeeping.
        try:
            from astrolift_operations.models import WebhookDelivery

            WebhookDelivery.objects.create(
                subscription=sub,
                event_type="webhook.test",
                retry_attempt=1,
                status_code=outcome["status_code"],
                latency_ms=int(outcome["duration_ms"] or 0),
                success=bool(
                    outcome["delivered"] and outcome["status_code"] and 200 <= outcome["status_code"] < 300
                ),
                is_test=True,
                request_payload_excerpt=json.dumps(payload, separators=(",", ":"))[:8192],
                response_body_excerpt=(outcome["response_body_excerpt"] or "")[:8192],
                error=(outcome["error"] or "")[:512],
                delivery_id=(outcome["delivery_id"] or "")[:64],
                delivered_at=datetime.fromtimestamp(outcome["timestamp_unix"], tz=UTC),
            )
        except Exception:
            log.exception(
                "failed to persist test webhook delivery row",
                extra={"subscription_id": str(sub.guid)},
            )

        return gql_success(
            WebhookTestResultType(
                subscription_id=input.id,
                url=sub.url,
                delivered=outcome["delivered"],
                status_code=outcome["status_code"],
                duration_ms=outcome["duration_ms"],
                response_body_excerpt=outcome["response_body_excerpt"],
                error=outcome["error"],
                delivery_id=outcome["delivery_id"],
                timestamp=datetime.fromtimestamp(outcome["timestamp_unix"], tz=UTC),
            )
        )

    @strawberry.field
    @mutation_audit(action="webhook.rotate_secret")
    @require_permission(Permission.WEBHOOK_UPDATE)
    @tenant_scoped()
    def rotate_outbound_webhook_secret(
        self, info: Info, input: RotateOutboundWebhookSecretInput
    ) -> MutationResultType[WebhookSecretReveal]:
        """Rotate the HMAC secret. Returns the new plaintext exactly
        once; the previous hash stays valid for the Constance grace
        window so subscribers can roll out without dropping
        deliveries.

        Audit-logged via ``mutation_audit`` so the rotation appears
        in the audit log; ``mutation_audit`` records the action +
        actor + target without leaking the plaintext.
        """
        from django.utils import timezone

        from astrolift_operations.webhook_rotation import (
            grace_seconds_from_constance,
            plan_rotation,
        )

        # Scope to the caller's org (#1183): this returns the new HMAC
        # secret plaintext once. A cross-org guid would hand a tenant
        # another tenant's fresh signing secret. org_id None →
        # deny-by-default (not-found).
        sub = WebhookSubscription.objects.filter(
            guid=str(input.id), organization_id=_caller_org_id(), deleted_at__isnull=True
        ).first()
        if sub is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "subscription not found",
                field="id",
            )

        grace_seconds = grace_seconds_from_constance()
        plan = plan_rotation(
            current_secret_hash=sub.secret_hash or "",
            now=timezone.now(),
            grace_seconds=grace_seconds,
        )

        sub.secret_hash_previous = plan.previous_secret_hash
        sub.secret_hash = plan.new_secret_hash
        sub.secret_rotated_at = plan.rotated_at
        sub.save(
            update_fields=[
                "secret_hash",
                "secret_hash_previous",
                "secret_rotated_at",
                "updated_at",
                "version",
            ]
        )

        return gql_success(
            WebhookSecretReveal(
                subscription=webhook_to_type(sub),
                plaintext_secret=plan.plaintext_secret,
            )
        )

    @strawberry.field
    @mutation_audit(action="notification.test")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def test_notification_channel(
        self, info: Info, input: TestNotificationInput
    ) -> MutationResultType[NotificationType]:
        """Create a SYSTEM-kind notification in the caller's inbox
        scoped to the given org.

        The 'channel' here is the in-app inbox — astrolift doesn't
        model standalone NotificationChannel rows; subscribers
        select channels per-AlertRule via ``notify_channels``. The
        test mutation surfaces a row the operator can see
        immediately in their notifications drawer so they know the
        fan-out path is wired."""
        from django.utils import timezone

        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "not authenticated",
            )

        org = Organization.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found", field="id")
        if tenant.organization_id is not None and tenant.organization_id != org.id:
            # Acting tenant must match the target org: operators can't
            # send themselves a test notification scoped to a different
            # tenant than the one they're currently in.
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "active tenant does not match target organization",
                field="id",
            )

        body = (input.message or "").strip() or (
            "This is a test notification from the Astrolift control plane. "
            "If you can see this in your inbox, the notification channel is wired correctly."
        )

        now = timezone.now()
        notif = Notification.objects.create(
            user_id=tenant.actor_user_id,
            organization=org,
            kind=Notification.Kind.SYSTEM,
            title="Test notification",
            body=body,
            link="",
        )
        # Stamp updated_at so it shows up at the top of the inbox.
        Notification.objects.filter(pk=notif.pk).update(updated_at=now)
        notif.refresh_from_db()
        return gql_success(notification_to_type(notif))

    @strawberry.field
    def mark_notification_read(
        self, info: Info, input: MarkNotificationReadInput
    ) -> MutationResultType[NotificationType]:
        from django.utils import timezone

        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")
        notif = Notification.objects.filter(guid=str(input.id), user_id=tenant.actor_user_id).first()
        if notif is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "notification not found")
        notif.read_at = timezone.now()
        notif.save(update_fields=["read_at", "updated_at", "version"])
        return gql_success(notification_to_type(notif))

    @strawberry.field
    def mark_all_notifications_read(self, info: Info) -> MutationResultType[_MarkAllReadPayload]:
        from django.utils import timezone

        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")
        marked = Notification.objects.filter(user_id=tenant.actor_user_id, read_at__isnull=True).update(
            read_at=timezone.now()
        )
        return gql_success(_MarkAllReadPayload(marked=marked))

    # ---- Push device registration (#476 §B) -----------------------

    @strawberry.field
    @mutation_audit(action="mobile_device.register")
    def register_mobile_device(
        self, info: Info, input: RegisterMobileDeviceInput
    ) -> MutationResultType[DeviceRegistrationType]:
        """Register a push-receivable device for the current user.

        Self-scoped — any authenticated user can register a device
        on their own account; no extra permission. The token
        uniqueness constraint covers re-registration: re-presenting
        a token resurrects the existing row (clears soft-delete +
        stale state) rather than inserting a duplicate.

        ``label`` is optional; surfaced in /settings/devices verbatim.
        Tokens up to 512 chars (FCM / APNs / web-push all fit).
        """
        from astrolift_operations.models import DeviceRegistration
        from astrolift_operations.notification_dispatch import (
            default_driver_slug_for_registration,
        )
        from astrolift_operations.schema.types import device_registration_to_type

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        platform = (input.platform or "").strip().lower()
        valid_platforms = {c for c, _ in DeviceRegistration.Platform.choices}
        if platform not in valid_platforms:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"platform must be one of {sorted(valid_platforms)}",
                field="platform",
            )
        token = (input.device_token or "").strip()
        if not token:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "device_token is required",
                field="deviceToken",
            )
        if len(token) > 512:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "device_token exceeds 512 characters",
                field="deviceToken",
            )
        label = (input.label or "").strip()[:200]

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None

        # Bind the registration to the caller's active AstroliftSession
        # row when one exists — the #499 dispatcher uses this to skip
        # echoing back to the device that just signed in.
        session_key = getattr(getattr(request, "session", None), "session_key", None) if request else None
        enrolled_session_id: int | None = None
        if session_key:
            from astrolift_identity.models import AstroliftSession

            row = (
                AstroliftSession.objects.filter(user_id=viewer.pk, session_key=session_key).only("pk").first()
            )
            if row is not None:
                enrolled_session_id = row.pk

        driver_slug = default_driver_slug_for_registration(organization_id=org_id)

        existing = (
            DeviceRegistration.all_objects.filter(user_id=viewer.pk, device_token=token)
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            updates: list[str] = []
            if existing.platform != platform:
                existing.platform = platform
                updates.append("platform")
            if label and existing.label != label:
                existing.label = label
                updates.append("label")
            if existing.driver != driver_slug:
                existing.driver = driver_slug
                updates.append("driver")
            if existing.organization_id != org_id:
                existing.organization_id = org_id
                updates.append("organization")
            if enrolled_session_id and existing.enrolled_session_id != enrolled_session_id:
                existing.enrolled_session_id = enrolled_session_id
                updates.append("enrolled_session")
            # Re-issued after a soft-delete / stale prune — clear both
            # so the row counts as live again.
            if existing.stale_at is not None or existing.deleted_at is not None:
                existing.stale_at = None
                existing.deleted_at = None
                updates += ["stale_at", "deleted_at"]
            if updates:
                existing.save(update_fields=updates + ["updated_at", "version"])
            return gql_success(device_registration_to_type(existing))

        row = DeviceRegistration.objects.create(
            user=viewer,
            device_token=token,
            platform=platform,
            label=label,
            organization_id=org_id,
            enrolled_session_id=enrolled_session_id,
            driver=driver_slug,
        )
        return gql_success(device_registration_to_type(row))

    @strawberry.field
    @mutation_audit(
        action="mobile_device.revoke",
        target=lambda self, info, input: ("device_registration", str(input.id)),
    )
    def revoke_mobile_device(
        self, info: Info, input: RevokeMobileDeviceInput
    ) -> MutationResultType[_RevokeMobileDevicePayload]:
        """Soft-delete one of the caller's push registrations.

        Self-only. Idempotent: revoking an already-revoked row
        returns ``ok: true`` with ``revoked=False``.
        """
        from astrolift_operations.models import DeviceRegistration

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        row = DeviceRegistration.all_objects.filter(guid=str(input.id)).first()
        if row is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "device not found")
        if row.user_id != viewer.pk:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "cannot revoke this device")

        already = row.deleted_at is not None
        if not already:
            row.soft_delete(by=viewer)
        return gql_success(_RevokeMobileDevicePayload(id=input.id, revoked=not already))

    # ---- Notification preferences (#476 §F, #499 §E) --------------

    @strawberry.field
    @mutation_audit(action="notification_preference.set")
    def set_notification_preference(
        self, info: Info, input: SetNotificationPreferenceInput
    ) -> MutationResultType[NotificationPreferenceType]:
        """Upsert one preference row for the caller.

        Self-only. ``enabled=True`` and ``enabled=False`` both create
        an explicit row that overrides the platform default. To
        revert to the default, the caller deletes the row via
        ``revokeNotificationPreference`` (no dedicated mutation
        today — the user just toggles back to the default value).
        """
        from astrolift_operations.models import (
            NotificationChannel,
            NotificationPreference,
        )
        from astrolift_operations.schema.types import notification_preference_to_type

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        channel = (input.channel or "").strip().lower()
        valid_channels = {c for c, _ in NotificationChannel.choices}
        if channel not in valid_channels:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"channel must be one of {sorted(valid_channels)}",
                field="channel",
            )
        event_kind = (input.event_kind or "").strip()
        if not event_kind:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "event_kind is required",
                field="eventKind",
            )
        if len(event_kind) > 64:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "event_kind exceeds 64 characters",
                field="eventKind",
            )

        row = NotificationPreference.objects.filter(
            user_id=viewer.pk,
            channel=channel,
            event_kind=event_kind,
        ).first()
        if row is None:
            row = NotificationPreference.objects.create(
                user=viewer,
                channel=channel,
                event_kind=event_kind,
                enabled=bool(input.enabled),
            )
        else:
            row.enabled = bool(input.enabled)
            row.save(update_fields=["enabled", "updated_at", "version"])
        return gql_success(notification_preference_to_type(row))

    # ---- Alert rules + events (#282) ------------------------------

    @strawberry.field
    @mutation_audit(action="alert_rule.create")
    @require_permission(Permission.WEBHOOK_CREATE)
    @tenant_scoped()
    def create_alert_rule(
        self,
        info: Info,
        input: CreateAlertRuleInput,
    ) -> MutationResultType[AlertRuleType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "no active organization",
            )
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "organization not found",
            )
        valid_targets = {t for t, _ in AlertRule.Target.choices}
        if input.target not in valid_targets:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"target must be one of {sorted(valid_targets)}",
                field="target",
            )
        valid_sev = {s for s, _ in AlertRule.Severity.choices}
        severity = (input.severity or "warn").lower()
        if severity not in valid_sev:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"severity must be one of {sorted(valid_sev)}",
                field="severity",
            )
        if input.target == AlertRule.Target.GLOBAL and input.target_id:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "global rules must not carry a target_id",
                field="targetId",
            )
        if AlertRule.objects.filter(
            organization=org,
            name=input.name.strip(),
            deleted_at__isnull=True,
        ).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"alert rule {input.name!r} already exists",
                field="name",
            )
        managed_service = None
        if input.managed_service_id is not None:
            from astrolift_services.models import ManagedService

            managed_service = ManagedService.objects.filter(
                guid=str(input.managed_service_id),
                deleted_at__isnull=True,
                registered_app__organization=org,
            ).first()
            if managed_service is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    "managed service not found",
                    field="managedServiceId",
                )
        rule = AlertRule.objects.create(
            organization=org,
            name=input.name.strip(),
            target=input.target,
            target_id=input.target_id or "",
            severity=severity,
            predicate=dict(input.predicate or {}),
            notify_channels=list(input.notify_channels or []),
            is_active=(True if input.is_active is None else bool(input.is_active)),
            managed_service=managed_service,
        )
        return gql_success(alert_rule_to_type(rule))

    @strawberry.field
    @mutation_audit(action="alert_rule.update")
    @require_permission(Permission.WEBHOOK_UPDATE)
    @tenant_scoped()
    def update_alert_rule(
        self,
        info: Info,
        input: UpdateAlertRuleInput,
    ) -> MutationResultType[AlertRuleType]:
        # Scope to the caller's org (#1183): a bare guid let any tenant
        # edit another tenant's alert rule (predicate, notify channels,
        # active). org_id None → deny-by-default (not-found).
        rule = AlertRule.objects.filter(
            guid=str(input.id), organization_id=_caller_org_id(), deleted_at__isnull=True
        ).first()
        if rule is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "alert rule not found",
            )
        if input.name is not None:
            rule.name = input.name.strip()
        if input.severity is not None:
            valid_sev = {s for s, _ in AlertRule.Severity.choices}
            if input.severity not in valid_sev:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"severity must be one of {sorted(valid_sev)}",
                    field="severity",
                )
            rule.severity = input.severity
        if input.predicate is not None:
            rule.predicate = dict(input.predicate)
        if input.notify_channels is not None:
            rule.notify_channels = list(input.notify_channels)
        if input.is_active is not None:
            rule.is_active = input.is_active
        if input.managed_service_id is not None:
            from astrolift_services.models import ManagedService

            tenant = get_current_tenant()
            org_id = tenant.organization_id if tenant else None
            service = ManagedService.objects.filter(
                guid=str(input.managed_service_id),
                deleted_at__isnull=True,
                registered_app__organization_id=org_id,
            ).first()
            if service is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    "managed service not found",
                    field="managedServiceId",
                )
            rule.managed_service = service
        rule.save()
        return gql_success(alert_rule_to_type(rule))

    @strawberry.field
    @mutation_audit(action="alert_rule.delete")
    @require_permission(Permission.WEBHOOK_DELETE)
    @tenant_scoped()
    def delete_alert_rule(
        self,
        info: Info,
        input: DeleteAlertRuleInput,
    ) -> MutationResultType[_AlertRuleDeletedPayload]:
        # Scope to the caller's org (#1183): without it any tenant could
        # soft-delete another tenant's alert rule by guid. org_id None →
        # deny-by-default (not-found).
        rule = AlertRule.objects.filter(
            guid=str(input.id), organization_id=_caller_org_id(), deleted_at__isnull=True
        ).first()
        if rule is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "alert rule not found",
            )
        rule.soft_delete()
        return gql_success(
            _AlertRuleDeletedPayload(
                id=input.id,
                deleted=True,
            )
        )

    @strawberry.field
    @mutation_audit(action="alert_event.acknowledge")
    @require_permission(Permission.WEBHOOK_UPDATE)
    @tenant_scoped()
    def acknowledge_alert_event(
        self,
        info: Info,
        input: AcknowledgeAlertEventInput,
    ) -> MutationResultType[AlertEventType]:
        # Scope through the owning rule's org (#1183): without it any
        # tenant could acknowledge another tenant's firing by guid.
        # org_id None → deny-by-default (not-found).
        event = AlertEvent.objects.filter(
            guid=str(input.id), rule__organization_id=_caller_org_id(), deleted_at__isnull=True
        ).first()
        if event is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "alert event not found",
            )
        if event.acknowledged_at is None:
            tenant = get_current_tenant()
            from django.contrib.auth import get_user_model
            from django.utils import timezone

            actor = None
            if tenant is not None and tenant.actor_user_id is not None:
                actor = get_user_model().objects.filter(pk=tenant.actor_user_id).first()
            event.acknowledged_at = timezone.now()
            event.acknowledged_by = actor
            event.save(
                update_fields=[
                    "acknowledged_at",
                    "acknowledged_by",
                    "updated_at",
                    "version",
                ]
            )
        return gql_success(alert_event_to_type(event))

    # ---- Alert mute (#434 scope C) --------------------------------

    @strawberry.field
    @mutation_audit(action="alert_rule.mute")
    @require_permission(Permission.WEBHOOK_UPDATE)
    @tenant_scoped()
    def mute_alert_rule(
        self,
        info: Info,
        input: MuteAlertRuleInput,
    ) -> MutationResultType[AlertRuleType]:
        """Silence an alert rule for the requested duration.

        Returns the rule with its ``activeMute`` field populated so
        the client can update the UI without a refetch. Reason is
        required so the audit log carries a human-readable answer
        to "why was this silenced?"."""
        from datetime import timedelta

        from django.contrib.auth import get_user_model
        from django.utils import timezone

        reason = (input.reason or "").strip()
        if not reason:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "reason is required",
                field="reason",
            )

        duration = int(input.duration_seconds or 0)
        if duration <= 0:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "durationSeconds must be positive",
                field="durationSeconds",
            )
        # Cap at 7 days so a forgotten mute can't silently outlive
        # the team's interest. Operators wanting longer should disable
        # the rule entirely.
        if duration > 7 * 24 * 60 * 60:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "durationSeconds cannot exceed 7 days (604800)",
                field="durationSeconds",
            )

        # Scope to the caller's org (#1183): muting is a cross-tenant
        # denial-of-visibility if a bare guid lets one tenant silence
        # another tenant's rule. org_id None → deny-by-default.
        rule = (
            AlertRule.objects.select_related("organization")
            .filter(guid=str(input.rule_id), organization_id=_caller_org_id(), deleted_at__isnull=True)
            .first()
        )
        if rule is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "alert rule not found",
                field="ruleId",
            )

        tenant = get_current_tenant()
        actor = None
        if tenant is not None and tenant.actor_user_id is not None:
            actor = get_user_model().objects.filter(pk=tenant.actor_user_id).first()

        AlertMute.objects.create(
            rule=rule,
            organization=rule.organization,
            ttl_until=timezone.now() + timedelta(seconds=duration),
            reason=reason,
            muted_by=actor,
        )
        return gql_success(alert_rule_to_type(rule))

    @strawberry.field
    @mutation_audit(action="alert_rule.unmute")
    @require_permission(Permission.WEBHOOK_UPDATE)
    @tenant_scoped()
    def unmute_alert_rule(
        self,
        info: Info,
        input: UnmuteAlertRuleInput,
    ) -> MutationResultType[AlertRuleType]:
        """Immediate unmute — soft-deletes every active mute on the
        rule so the next firing fans out to channels.

        Idempotent: returns ok=true even when no active mute exists.
        Mute history rows stay around for the audit log; only the
        *active* mutes are cleared."""
        from django.utils import timezone

        # Scope to the caller's org (#1183): unmuting another tenant's
        # rule would re-arm their alert fan-out. org_id None →
        # deny-by-default (not-found).
        rule = (
            AlertRule.objects.select_related("organization")
            .filter(guid=str(input.rule_id), organization_id=_caller_org_id(), deleted_at__isnull=True)
            .first()
        )
        if rule is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "alert rule not found",
                field="ruleId",
            )
        active_mutes = AlertMute.objects.filter(
            rule=rule,
            deleted_at__isnull=True,
            ttl_until__gt=timezone.now(),
        )
        for mute in active_mutes:
            # ``soft_delete`` stamps deleted_at + bumps version; the
            # delivery worker's ``is_rule_muted`` filters those out.
            mute.soft_delete()
        return gql_success(alert_rule_to_type(rule))

    @strawberry.field
    @mutation_audit(action="audit_log.export")
    @require_permission(Permission.AUDIT_LOG_EXPORT)
    @tenant_scoped()
    def export_audit_events(
        self, info: Info, input: ExportAuditEventsInput
    ) -> MutationResultType[AuditExportType]:
        """Stream the matching audit slice into a token-gated download
        (#433). The mutation persists an :class:`AuditExport` row and
        returns the pre-signed URL + TTL + integrity hash. Honours the
        same filters as ``astroliftAuditEventsPage`` so the download
        matches what the operator sees on screen."""
        from datetime import timedelta

        from constance import config as constance_config
        from django.utils import timezone

        from astrolift_operations.audit_export import (
            hash_token,
            mint_token,
            write_artifact,
        )

        fmt = (input.format or "").strip().lower()
        if fmt not in {"csv", "ndjson"}:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "format must be CSV or NDJSON",
                field="format",
            )

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        # Build the queryset under the exact same filter contract as
        # the page query. Order ascending so the export reads
        # naturally for an auditor (oldest -> newest).
        #
        # Scope the exported rows to the caller's org (#1183). The
        # AuditExport row below stamps organization=org, but WITHOUT
        # this clause on the source queryset the streamed artifact
        # contained every tenant's audit trail (bulk cross-org PII
        # exfil). org_id is non-None here (guarded above). Mirrors the
        # org scope on astroliftAuditEventsPage.
        qs = AuditEvent.objects.filter(organization_id=org_id).order_by("occurred_at", "guid")
        if input.action:
            qs = qs.filter(action=input.action)
        if input.decision:
            qs = qs.filter(decision=input.decision.upper())
        if input.actor_id:
            qs = qs.filter(actor_id=input.actor_id)
        if input.created_at_gte is not None:
            qs = qs.filter(occurred_at__gte=input.created_at_gte)
        if input.created_at_lte is not None:
            qs = qs.filter(occurred_at__lte=input.created_at_lte)

        max_rows = max(1, int(getattr(constance_config, "AUDIT_EXPORT_MAX_ROWS", 100000)))
        candidate_count = qs.count()
        if candidate_count > max_rows:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                (
                    f"export would produce {candidate_count} rows, exceeding the "
                    f"AUDIT_EXPORT_MAX_ROWS cap ({max_rows}); narrow the date range "
                    "or actor/action filter and retry"
                ),
            )

        # UUIDv7 default fills in on save, but the artifact filename
        # needs the guid before we hit the DB — generate explicitly so
        # the file path + DB row agree.
        from core.fields.uuid_v7 import uuid7

        export_guid = uuid7()

        artifact = write_artifact(
            qs.iterator(chunk_size=500),
            format=fmt,
            guid=str(export_guid),
        )

        plaintext_token, token_hash = mint_token()
        ttl_seconds = max(
            60,
            int(getattr(constance_config, "AUDIT_EXPORT_DOWNLOAD_TTL_SECONDS", 3600)),
        )
        expires_at = timezone.now() + timedelta(seconds=ttl_seconds)

        actor_user_id = tenant.actor_user_id if tenant else None
        from django.contrib.auth import get_user_model

        requested_by = None
        if actor_user_id is not None:
            requested_by = get_user_model().objects.filter(pk=actor_user_id).first()

        export = AuditExport.objects.create(
            guid=export_guid,
            organization=org,
            requested_by=requested_by,
            format=fmt,
            row_count=artifact.row_count,
            byte_count=artifact.byte_count,
            sha256=artifact.sha256,
            relative_path=artifact.relative_path,
            token_hash=token_hash,
            filters_snapshot={
                "action": input.action or "",
                "decision": (input.decision or "").upper() or "",
                "actor_id": input.actor_id or "",
                "created_at_gte": (input.created_at_gte.isoformat() if input.created_at_gte else ""),
                "created_at_lte": (input.created_at_lte.isoformat() if input.created_at_lte else ""),
            },
            expires_at=expires_at,
        )
        # ``token_hash`` here is computed from the plaintext so a later
        # download-view check matches.
        assert export.token_hash == hash_token(plaintext_token)

        download_url = _build_audit_export_url(
            info=info,
            guid=str(export.guid),
            token=plaintext_token,
        )
        return gql_success(audit_export_to_type(export, download_url=download_url))

    @strawberry.field
    @mutation_audit(action="app.log_export")
    @require_permission(Permission.APP_LOG_EXPORT)
    @tenant_scoped()
    def export_astrolift_app_logs(
        self, info: Info, input: ExportAppLogsInput
    ) -> MutationResultType[AppLogExportType]:
        """Stream the matching app-log slice into a token-gated
        download (#483). Same artifact + single-use token shape as
        ``exportAuditEvents``; pulls runtime container logs through
        the cluster ``ClusterDriver`` (the same path the ``onAppLog``
        subscription uses) so what the operator sees on screen is
        what lands in the file."""
        import re

        from constance import config as constance_config
        from django.utils import timezone

        from astrolift_lifecycle.models import AppEnvironment
        from astrolift_operations import app_log_export as app_log_helpers
        from astrolift_operations.models import AppLogExport
        from astrolift_registry.models import RegisteredApp
        from core.cluster_observability import (
            ClusterObservabilityError,
            namespace_for_app,
        )

        fmt = (input.format or "").strip().lower()
        if fmt not in {"csv", "ndjson", "txt"}:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "format must be CSV, NDJSON, or TXT",
                field="format",
            )

        if not (input.app_slug or "").strip():
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "appSlug is required",
                field="appSlug",
            )

        if not (input.pod_name or "").strip():
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "podName is required",
                field="podName",
            )

        if input.regex:
            try:
                re.compile(input.regex)
            except re.error as exc:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"invalid regex: {exc}",
                    field="regex",
                )

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        app = (
            RegisteredApp.objects.select_related("organization", "default_tenant_cluster")
            .filter(
                slug=input.app_slug,
                organization_id=org_id,
                deleted_at__isnull=True,
            )
            .first()
        )
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"app {input.app_slug!r} not found",
                field="appSlug",
            )

        # Environment lookup: when the operator pinned a name, that
        # env's cluster wins; otherwise we fall back to the app's
        # default cluster (matches the subscription's resolution
        # contract). An unknown env name fails loudly so a typo
        # doesn't silently land logs from the wrong cluster.
        cluster = None
        if input.environment_name:
            env = (
                AppEnvironment.objects.select_related("tenant_cluster")
                .filter(
                    registered_app=app,
                    name=input.environment_name,
                    deleted_at__isnull=True,
                )
                .first()
            )
            if env is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    f"environment {input.environment_name!r} not found on app {app.slug!r}",
                    field="environmentName",
                )
            cluster = env.tenant_cluster
        if cluster is None:
            cluster = app.default_tenant_cluster
        if cluster is None or not getattr(cluster, "is_active", True):
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"app {app.slug!r} has no active cluster wired",
            )

        namespace = namespace_for_app(app)

        max_lines = max(
            1,
            int(getattr(constance_config, "APP_LOG_EXPORT_MAX_LINES", 100000)),
        )

        # Pull from the cluster driver with ``follow=False`` so the
        # generator terminates at the current tail. ``tail_lines``
        # is capped at the configured max so the driver doesn't ship
        # us more than the export will ever serialize.
        try:
            line_source = _materialize_app_log_lines(
                cluster=cluster,
                namespace=namespace,
                pod_name=input.pod_name,
                container=input.container,
                tail_lines=max_lines,
                since=input.since,
                until=input.until,
            )
        except ClusterObservabilityError as exc:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"cluster log backend unavailable: {exc}",
            )

        from core.fields.uuid_v7 import uuid7

        export_guid = uuid7()

        try:
            artifact = app_log_helpers.write_artifact(
                line_source,
                format=fmt,
                guid=str(export_guid),
                max_lines=max_lines,
                level=input.level,
                regex=input.regex,
            )
        except ValueError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc), field="format")

        plaintext_token, token_hash = app_log_helpers.mint_token()
        ttl_seconds = max(
            60,
            int(getattr(constance_config, "APP_LOG_EXPORT_DOWNLOAD_TTL_SECONDS", 3600)),
        )
        expires_at = timezone.now() + dt.timedelta(seconds=ttl_seconds)

        actor_user_id = tenant.actor_user_id if tenant else None
        from django.contrib.auth import get_user_model

        requested_by = None
        if actor_user_id is not None:
            requested_by = get_user_model().objects.filter(pk=actor_user_id).first()

        export = AppLogExport.objects.create(
            guid=export_guid,
            organization=org,
            registered_app=app,
            environment_name=input.environment_name or "",
            pod_name=input.pod_name or "",
            workload_name=input.workload_slug or "",
            container=input.container or "",
            requested_by=requested_by,
            format=fmt,
            status=AppLogExport.Status.READY,
            row_count=artifact.row_count,
            byte_count=artifact.byte_count,
            sha256=artifact.sha256,
            relative_path=artifact.relative_path,
            token_hash=token_hash,
            filters_snapshot={
                "since": (input.since.isoformat() if input.since else ""),
                "until": (input.until.isoformat() if input.until else ""),
                "level": input.level or "",
                "regex": input.regex or "",
                "container": input.container or "",
                "workload_slug": input.workload_slug or "",
                "truncated": artifact.truncated,
            },
            expires_at=expires_at,
        )
        assert export.token_hash == app_log_helpers.hash_token(plaintext_token)

        download_url = _build_app_log_export_url(
            info=info,
            guid=str(export.guid),
            token=plaintext_token,
        )
        return gql_success(app_log_export_to_type(export, download_url=download_url))

    # ---- Alert subscriptions (#747) --------------------------------

    @strawberry.field
    @mutation_audit(action="alert_subscription.set")
    @tenant_scoped()
    def set_alert_subscription(
        self,
        info: Info,
        input: SetAlertSubscriptionInput,
    ) -> MutationResultType[UserAlertSubscriptionType]:
        """Upsert the caller's per-app alert notification preference (#747).

        Creates a new subscription row or updates the existing active
        one for the same (user, app, alert_kind) tuple. Soft-deletes
        the previous row when upserting so the audit trail is intact.
        """
        from astrolift_registry.models import RegisteredApp

        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "authentication required")

        valid_kinds = {k for k, _ in UserAlertSubscription.AlertKind.choices}
        if input.alert_kind not in valid_kinds:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"alert_kind must be one of {sorted(valid_kinds)}",
                field="alert_kind",
            )
        valid_channels = {c for c, _ in UserAlertSubscription.Channel.choices}
        if input.channel not in valid_channels:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"channel must be one of {sorted(valid_channels)}",
                field="channel",
            )

        # Scope the app lookup to the caller's org (#1183): slugs are
        # unique per-org, so an unscoped lookup let a caller bind a
        # subscription to (and confirm the existence of) a same-slug app
        # in another tenant. tenant is non-None here (guarded above).
        app = RegisteredApp.objects.filter(
            slug=input.app_slug,
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
        ).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, f"app '{input.app_slug}' not found")

        existing = UserAlertSubscription.objects.filter(
            user_id=tenant.actor_user_id,
            registered_app=app,
            alert_kind=input.alert_kind,
            deleted_at__isnull=True,
        ).first()
        if existing is not None:
            existing.channel = input.channel
            existing.enabled = input.enabled
            existing.save(update_fields=["channel", "enabled", "updated_at", "version"])
            return gql_success(user_alert_subscription_to_type(existing))

        sub = UserAlertSubscription.objects.create(
            user_id=tenant.actor_user_id,
            registered_app=app,
            alert_kind=input.alert_kind,
            channel=input.channel,
            enabled=input.enabled,
        )
        return gql_success(user_alert_subscription_to_type(sub))

    @strawberry.field
    @mutation_audit(action="alert_subscription.clear")
    @tenant_scoped()
    def clear_alert_subscription(
        self,
        info: Info,
        input: ClearAlertSubscriptionInput,
    ) -> MutationResultType[UserAlertSubscriptionType]:
        """Soft-delete a per-app alert subscription (#747).

        Reverts the (user, app, alert_kind) slot to the dispatcher's
        noisy-fallback default. Uses the GUID returned by
        ``setAlertSubscription`` or ``myAlertSubscriptions``.
        """
        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "authentication required")

        sub = UserAlertSubscription.objects.filter(
            guid=input.id,
            user_id=tenant.actor_user_id,
            deleted_at__isnull=True,
        ).first()
        if sub is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "subscription not found")

        snapshot = user_alert_subscription_to_type(sub)
        sub.soft_delete()
        return gql_success(snapshot)

    # ---- Bulk app operations (#746) --------------------------------

    @strawberry.field
    @mutation_audit(action="app.bulk.rolling_restart")
    @require_permission(Permission.APP_DEPLOY)
    @tenant_scoped()
    def bulk_rolling_restart(
        self,
        info: Info,
        input: BulkRollingRestartInput,
    ) -> BulkOperationResult:
        """Rolling-restart all workloads across a selection of apps (#746).

        Fans out ``rollout_restart_workload`` per workload per app. Capped
        at ``_BULK_APP_CAP`` apps per call. Per-app failures are collected
        rather than short-circuiting so the FE can render a
        success+failure summary in one toast."""
        from astrolift_lifecycle.services.k8s_ops import (
            K8sOpError,
            rollout_restart_workload,
        )
        from astrolift_registry.models import RegisteredApp, Workload

        slugs = list(dict.fromkeys(input.app_slugs or []))[:_BULK_APP_CAP]
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        # Fail closed without a tenant org (#1192): the per-slug app lookup is
        # org-scoped, so a None org must not fall through to an unscoped
        # by-slug fetch. Mirrors the fail-closed bulk_push_secrets guard.
        if org_id is None:
            return BulkOperationResult(
                ok_count=0,
                failed_count=len(slugs),
                per_app=[
                    BulkAppResultItem(app_slug=s, ok=False, errors=["no active organization"]) for s in slugs
                ],
            )

        per_app: list[BulkAppResultItem] = []
        for slug in slugs:
            app = RegisteredApp.objects.filter(
                slug=slug,
                organization_id=org_id,
                deleted_at__isnull=True,
            ).first()
            if app is None:
                per_app.append(BulkAppResultItem(app_slug=slug, ok=False, errors=["app not found"]))
                continue

            workload_qs = Workload.objects.filter(
                registered_app=app,
                deleted_at__isnull=True,
            )

            workloads = list(workload_qs.select_related("registered_app"))
            if not workloads:
                per_app.append(
                    BulkAppResultItem(app_slug=slug, ok=False, errors=["no active workloads found"])
                )
                continue

            errors: list[str] = []
            for wl in workloads:
                try:
                    rollout_restart_workload(wl)
                except K8sOpError as exc:
                    errors.append(f"{wl.slug}: {exc.message}")

            per_app.append(BulkAppResultItem(app_slug=slug, ok=not errors, errors=errors))

        ok_count = sum(1 for r in per_app if r.ok)
        return BulkOperationResult(
            ok_count=ok_count,
            failed_count=len(per_app) - ok_count,
            per_app=per_app,
        )

    @strawberry.field
    @mutation_audit(action="app.bulk.push_secrets")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def bulk_push_secrets(
        self,
        info: Info,
        input: BulkPushSecretsInput,
    ) -> BulkOperationResult:
        """Attach a shared ``SecretBundle`` to a selection of apps (#746).

        For each app, looks up the target ``AppEnvironment`` by name (uses
        the first active environment when ``environmentName`` is null).
        If the bundle is already attached to that environment's ref, the
        existing row is left in place (idempotent). The bundle's key-value
        pairs are injected on the next deploy or manifest reconcile."""
        from astrolift_lifecycle.models import AppEnvironment
        from astrolift_registry.models import RegisteredApp
        from astrolift_services.models.secret_bundle import AppSecretBundleRef, SecretBundle

        slugs = list(dict.fromkeys(input.app_slugs or []))[:_BULK_APP_CAP]
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        # Deny-by-default without a tenant context (#1183): every lookup
        # below is org-scoped, so a None org must fail closed rather than
        # match NULL-org / cross-org rows.
        if org_id is None:
            return BulkOperationResult(
                ok_count=0,
                failed_count=len(slugs),
                per_app=[
                    BulkAppResultItem(app_slug=s, ok=False, errors=["no active organization"]) for s in slugs
                ],
            )

        # Scope the bundle to the caller's org (#1183): SecretBundle owns
        # an organization FK. An unscoped slug lookup let a caller attach
        # ANOTHER tenant's secret bundle to their own apps — cross-org
        # secret injection on the next deploy / manifest reconcile.
        bundle = SecretBundle.objects.filter(
            slug=input.bundle_slug,
            organization_id=org_id,
            deleted_at__isnull=True,
        ).first()
        if bundle is None:
            return BulkOperationResult(
                ok_count=0,
                failed_count=len(slugs),
                per_app=[
                    BulkAppResultItem(
                        app_slug=s,
                        ok=False,
                        errors=[f"bundle {input.bundle_slug!r} not found"],
                    )
                    for s in slugs
                ],
            )

        per_app: list[BulkAppResultItem] = []
        for slug in slugs:
            app = RegisteredApp.objects.filter(
                slug=slug,
                organization_id=org_id,
                deleted_at__isnull=True,
            ).first()
            if app is None:
                per_app.append(BulkAppResultItem(app_slug=slug, ok=False, errors=["app not found"]))
                continue

            env_qs = AppEnvironment.objects.filter(
                registered_app=app,
                deleted_at__isnull=True,
            )
            if input.environment_name:
                env_qs = env_qs.filter(name=input.environment_name)
            env = env_qs.order_by("id").first()
            if env is None:
                per_app.append(BulkAppResultItem(app_slug=slug, ok=False, errors=["no matching environment"]))
                continue

            exists = AppSecretBundleRef.objects.filter(
                registered_app=app,
                app_environment=env,
                secret_bundle=bundle,
                deleted_at__isnull=True,
            ).exists()
            if not exists:
                try:
                    AppSecretBundleRef.objects.create(
                        registered_app=app,
                        app_environment=env,
                        secret_bundle=bundle,
                    )
                except Exception as exc:  # noqa: BLE001
                    per_app.append(BulkAppResultItem(app_slug=slug, ok=False, errors=[str(exc)[:256]]))
                    continue

            per_app.append(BulkAppResultItem(app_slug=slug, ok=True, errors=[]))

        ok_count = sum(1 for r in per_app if r.ok)
        return BulkOperationResult(
            ok_count=ok_count,
            failed_count=len(per_app) - ok_count,
            per_app=per_app,
        )

    @strawberry.field
    @mutation_audit(action="app.bulk.resync_manifest")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def bulk_resync_manifest(
        self,
        info: Info,
        input: BulkResyncManifestInput,
    ) -> BulkOperationResult:
        """Re-fetch and apply the manifest from the source repo for a
        selection of apps (#746). Fans out
        ``resync_app_manifest_from_repo`` per app. Per-app failures are
        collected so the FE can render a summary."""
        from astrolift_registry.models import RegisteredApp
        from astrolift_registry.services.manifest_sync import resync_app_manifest_from_repo

        slugs = list(dict.fromkeys(input.app_slugs or []))[:_BULK_APP_CAP]
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        # Fail closed without a tenant org (#1192): the per-slug app lookup is
        # org-scoped, so a None org must not fall through to an unscoped
        # by-slug fetch. Mirrors the fail-closed bulk_push_secrets guard.
        if org_id is None:
            return BulkOperationResult(
                ok_count=0,
                failed_count=len(slugs),
                per_app=[
                    BulkAppResultItem(app_slug=s, ok=False, errors=["no active organization"]) for s in slugs
                ],
            )

        per_app: list[BulkAppResultItem] = []
        for slug in slugs:
            app = RegisteredApp.objects.filter(
                slug=slug,
                organization_id=org_id,
                deleted_at__isnull=True,
            ).first()
            if app is None:
                per_app.append(BulkAppResultItem(app_slug=slug, ok=False, errors=["app not found"]))
                continue

            try:
                result = resync_app_manifest_from_repo(app)
            except Exception as exc:  # noqa: BLE001
                per_app.append(BulkAppResultItem(app_slug=slug, ok=False, errors=[str(exc)[:256]]))
                continue

            if result.status in ("applied", "in_sync"):
                per_app.append(BulkAppResultItem(app_slug=slug, ok=True, errors=[]))
            else:
                per_app.append(
                    BulkAppResultItem(
                        app_slug=slug,
                        ok=False,
                        errors=[result.error or f"sync status: {result.status}"],
                    )
                )

        ok_count = sum(1 for r in per_app if r.ok)
        return BulkOperationResult(
            ok_count=ok_count,
            failed_count=len(per_app) - ok_count,
            per_app=per_app,
        )


def _materialize_app_log_lines(
    *,
    cluster,
    namespace: str,
    pod_name: str | None,
    container: str | None,
    tail_lines: int,
    since: dt.datetime | None,
    until: dt.datetime | None,
):
    """Drain the cluster driver's async log generator into a list
    bounded by ``tail_lines``.

    The export resolver is a synchronous mutation but the driver
    returns an :class:`AsyncIterator` of ``PodLogLine`` instances.
    We collect with a fresh event loop and clamp to ``tail_lines``
    so a chatty pod can't blow the resolver's memory budget. The
    ``since`` / ``until`` filter runs in-process — the cluster
    driver's k8s ``--since-time=`` path is per-plugin and out of
    scope for the first cut.
    """
    import asyncio

    from core.cluster_observability import stream_app_logs

    async def _drain():
        collected: list = []
        # Ask for one more than the configured cap so the serializer
        # can honestly distinguish "cap hit" from "stream ended at
        # exactly the cap". The serializer drops the spare in either
        # case — only the truncation flag depends on it.
        gen = stream_app_logs(
            cluster=cluster,
            namespace=namespace,
            pod_name=pod_name or "",
            container=container,
            tail_lines=tail_lines + 1,
            follow=False,
        )
        try:
            async for line in gen:
                ts = getattr(line, "timestamp", None)
                if since is not None and ts is not None and ts < since:
                    continue
                if until is not None and ts is not None and ts > until:
                    continue
                collected.append(line)
                if len(collected) >= tail_lines + 1:
                    # +1 over the cap lets the serializer detect
                    # truncation honestly.
                    break
        finally:
            try:
                await gen.aclose()
            except Exception:  # noqa: BLE001
                pass
        return collected

    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_drain())
        finally:
            loop.close()
    except RuntimeError:
        # An outer event loop is already running (rare for a sync
        # resolver; protects against pytest-asyncio harness misuse).
        return asyncio.run(_drain())


def _build_audit_export_url(*, info: Info, guid: str, token: str) -> str:
    """Build the absolute download URL for an audit export. Uses the
    incoming request to honor the public base URL when reverse-proxied
    (X-Forwarded-Host); falls back to ``PLATFORM_API_URL`` for cases
    where the resolver runs outside a request context."""
    from django.conf import settings

    base_url_setting = getattr(settings, "DJANGO_BASE_URL", None) or getattr(settings, "BASE_URL", "app/")
    base_url = (base_url_setting or "app/").lstrip("/")
    relative = f"/{base_url}audit_exports/{guid}/{token}/"

    request = getattr(info.context, "request", None) if info and info.context else None
    if request is not None:
        try:
            return request.build_absolute_uri(relative)
        except Exception:  # noqa: BLE001 — never let URL building break the mutation
            pass

    platform_url = (getattr(settings, "PLATFORM_API_URL", "") or "").rstrip("/")
    if platform_url:
        return platform_url + relative
    return relative


def _build_app_log_export_url(*, info: Info, guid: str, token: str) -> str:
    """Build the absolute download URL for an app-log export (#483).
    Same shape as the audit-export URL builder but mounted under
    ``/app/app_log_exports/`` per :mod:`astrolift_operations.urls`."""
    from django.conf import settings

    base_url_setting = getattr(settings, "DJANGO_BASE_URL", None) or getattr(settings, "BASE_URL", "app/")
    base_url = (base_url_setting or "app/").lstrip("/")
    relative = f"/{base_url}app_log_exports/{guid}/{token}/"

    request = getattr(info.context, "request", None) if info and info.context else None
    if request is not None:
        try:
            return request.build_absolute_uri(relative)
        except Exception:  # noqa: BLE001 — never let URL building break the mutation
            pass

    platform_url = (getattr(settings, "PLATFORM_API_URL", "") or "").rstrip("/")
    if platform_url:
        return platform_url + relative
    return relative
