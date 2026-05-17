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
    WebhookSubscription,
)
from astrolift_operations.notification_dispatch import (
    register_device,
    unregister_device,
)
from astrolift_operations.schema.types import (
    AlertEventType,
    AlertRuleType,
    AuditExportType,
    DeviceRegistrationType,
    NotificationType,
    WebhookSubscriptionType,
    WebhookTestResultType,
    alert_event_to_type,
    alert_rule_to_type,
    audit_export_to_type,
    device_registration_to_type,
    notification_to_type,
    webhook_to_type,
)
from astrolift_operations.webhook_delivery import build_headers, sign_payload
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

log = logging.getLogger(__name__)


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


@strawberry.input
class RegisterAstroliftDeviceInput:
    """Register a push device for the caller (#490).

    ``token`` is the platform-supplied push token (APNs hex, FCM
    string, or Web Push endpoint). ``kind`` is the device platform;
    must be one of ``ios`` / ``android`` / ``web``. ``platformData``
    is an opaque blob persisted on the registration row for
    driver-side metadata (e.g. ANH tag list, SNS app ARN hint)."""

    token: str
    kind: str
    label: str | None = None
    platform_data: strawberry.scalars.JSON | None = None


@strawberry.input
class UnregisterAstroliftDeviceInput:
    """Unregister one of the caller's devices. The device GUID is
    returned by ``registerAstroliftDevice`` + ``astroliftMyDevices``."""

    device_id: GUID


@strawberry.type
class _MarkAllReadPayload:
    marked: int


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


@strawberry.input
class UpdateAlertRuleInput:
    id: GUID
    name: str | None = None
    severity: str | None = None
    predicate: strawberry.scalars.JSON | None = None
    notify_channels: strawberry.scalars.JSON | None = None
    is_active: bool | None = None


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
        sub = WebhookSubscription.objects.filter(guid=str(input.id)).first()
        if sub is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "subscription not found")
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
        sub = WebhookSubscription.objects.filter(guid=str(input.id)).first()
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

        sub = WebhookSubscription.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
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

        sub = WebhookSubscription.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
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

    # ---- Device registration (#490) -------------------------------

    @strawberry.field
    @mutation_audit(action="notification.device.register")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def register_astrolift_device(
        self,
        info: Info,
        input: RegisterAstroliftDeviceInput,
    ) -> MutationResultType[DeviceRegistrationType]:
        """Register a push device for the caller.

        Idempotent on (user, token): re-registering the same token
        returns the existing row with the label optionally refreshed.
        When the org has an active NotificationProfile, the dispatcher
        also calls the driver's ``register_device`` so the
        provider-side endpoint exists before any send."""
        tenant = get_current_tenant()
        if (
            tenant is None
            or tenant.actor_user_id is None
            or tenant.organization_id is None
        ):
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value, "not authenticated",
            )
        kind = (input.kind or "").strip().lower()
        if kind not in {"ios", "android", "web"}:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "kind must be one of ios | android | web",
                field="kind",
            )
        token = (input.token or "").strip()
        if not token:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "token is required",
                field="token",
            )
        try:
            result = register_device(
                organization_id=tenant.organization_id,
                user_id=tenant.actor_user_id,
                device_token=token,
                platform=kind,
                label=(input.label or "").strip(),
            )
        except Exception as exc:  # noqa: BLE001
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"device registration failed: {exc}",
            )
        return gql_success(device_registration_to_type(result.device))

    @strawberry.field
    @mutation_audit(action="notification.device.unregister")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def unregister_astrolift_device(
        self,
        info: Info,
        input: UnregisterAstroliftDeviceInput,
    ) -> MutationResultType[_SoftDeletePayload]:
        """Revoke a device. Soft-deletes the row + best-effort
        revokes the provider-side endpoint."""
        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value, "not authenticated",
            )
        ok = unregister_device(
            device_guid=str(input.device_id),
            user_id=tenant.actor_user_id,
        )
        if not ok:
            return gql_failure(
                ErrorCode.NOT_FOUND.value, "device not found",
            )
        return gql_success(
            _SoftDeletePayload(id=input.device_id, deleted=True),
        )

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
        rule = AlertRule.objects.create(
            organization=org,
            name=input.name.strip(),
            target=input.target,
            target_id=input.target_id or "",
            severity=severity,
            predicate=dict(input.predicate or {}),
            notify_channels=list(input.notify_channels or []),
            is_active=(True if input.is_active is None else bool(input.is_active)),
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
        rule = AlertRule.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
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
        rule = AlertRule.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
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
        event = AlertEvent.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
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

        rule = (
            AlertRule.objects.select_related("organization")
            .filter(guid=str(input.rule_id), deleted_at__isnull=True)
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

        rule = (
            AlertRule.objects.select_related("organization")
            .filter(guid=str(input.rule_id), deleted_at__isnull=True)
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
        qs = AuditEvent.objects.order_by("occurred_at", "guid")
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
