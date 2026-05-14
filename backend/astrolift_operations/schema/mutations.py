"""Mutations for the operations app: webhook CRUD + notification mark-read."""

from __future__ import annotations

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
    AlertRule,
    Notification,
    WebhookSubscription,
)
from astrolift_operations.schema.types import (
    AlertEventType,
    AlertRuleType,
    NotificationType,
    WebhookSubscriptionType,
    WebhookTestResultType,
    alert_event_to_type,
    alert_rule_to_type,
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


@strawberry.input
class UpdateWebhookSubscriptionInput:
    id: GUID
    url: str | None = None
    events: list[str] | None = None
    is_active: bool | None = None


@strawberry.input
class DeleteWebhookSubscriptionInput:
    id: GUID


@strawberry.input
class MarkNotificationReadInput:
    id: GUID


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
) -> dict:
    """POST ``payload`` to ``url`` with the standard webhook headers
    + HMAC signature. Returns a result dict the caller folds into
    :class:`WebhookTestResultType`.

    Synchronous on purpose: the real DeliverWebhookWorkflow handles
    retries + backoff, but a manual test wants the immediate verdict
    so the operator can wire the integration without leaving the UI.
    """
    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
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

        sub = WebhookSubscription.objects.create(
            organization=org,
            team=team,
            registered_app=registered_app,
            url=input.url.strip(),
            secret_hash=digest,
            events=list(input.events or []),
            is_active=True,
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
