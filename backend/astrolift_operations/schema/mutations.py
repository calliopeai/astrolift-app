"""Mutations for the operations app: webhook CRUD + notification mark-read."""

from __future__ import annotations

import hashlib
import secrets

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
    alert_event_to_type,
    alert_rule_to_type,
    notification_to_type,
    webhook_to_type,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


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

            registered_app = (
                RegisteredApp.objects
                .filter(organization=org, slug=input.app_slug)
                .first()
            )
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
        self, info: Info, input: CreateAlertRuleInput,
    ) -> MutationResultType[AlertRuleType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(
                ErrorCode.PRECONDITION.value, "no active organization",
            )
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value, "organization not found",
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
        if (
            AlertRule.objects.filter(
                organization=org,
                name=input.name.strip(),
                deleted_at__isnull=True,
            ).exists()
        ):
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
            is_active=(
                True if input.is_active is None else bool(input.is_active)
            ),
        )
        return gql_success(alert_rule_to_type(rule))

    @strawberry.field
    @mutation_audit(action="alert_rule.update")
    @require_permission(Permission.WEBHOOK_UPDATE)
    @tenant_scoped()
    def update_alert_rule(
        self, info: Info, input: UpdateAlertRuleInput,
    ) -> MutationResultType[AlertRuleType]:
        rule = (
            AlertRule.objects
            .filter(guid=str(input.id), deleted_at__isnull=True)
            .first()
        )
        if rule is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value, "alert rule not found",
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
        self, info: Info, input: DeleteAlertRuleInput,
    ) -> MutationResultType[_AlertRuleDeletedPayload]:
        rule = (
            AlertRule.objects
            .filter(guid=str(input.id), deleted_at__isnull=True)
            .first()
        )
        if rule is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value, "alert rule not found",
            )
        rule.soft_delete()
        return gql_success(_AlertRuleDeletedPayload(
            id=input.id, deleted=True,
        ))

    @strawberry.field
    @mutation_audit(action="alert_event.acknowledge")
    @require_permission(Permission.WEBHOOK_UPDATE)
    @tenant_scoped()
    def acknowledge_alert_event(
        self, info: Info, input: AcknowledgeAlertEventInput,
    ) -> MutationResultType[AlertEventType]:
        event = (
            AlertEvent.objects
            .filter(guid=str(input.id), deleted_at__isnull=True)
            .first()
        )
        if event is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value, "alert event not found",
            )
        if event.acknowledged_at is None:
            tenant = get_current_tenant()
            from django.contrib.auth import get_user_model

            actor = None
            if tenant is not None and tenant.actor_user_id is not None:
                actor = (
                    get_user_model().objects
                    .filter(pk=tenant.actor_user_id)
                    .first()
                )
            event.acknowledged_at = timezone.now()
            event.acknowledged_by = actor
            event.save(update_fields=[
                "acknowledged_at", "acknowledged_by",
                "updated_at", "version",
            ])
        return gql_success(alert_event_to_type(event))
