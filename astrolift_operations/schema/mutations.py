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
from astrolift_operations.models import Notification, WebhookSubscription
from astrolift_operations.schema.types import (
    NotificationType,
    WebhookSubscriptionType,
    notification_to_type,
    webhook_to_type,
)
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.input
class CreateWebhookSubscriptionInput:
    url: str
    events: list[str]
    team_slug: str | None = None


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
                return gql_failure(
                    ErrorCode.NOT_FOUND.value, "team not found", field="teamSlug"
                )

        plaintext_secret = "alfthk_" + secrets.token_urlsafe(24)
        digest = hashlib.sha256(plaintext_secret.encode()).hexdigest()

        sub = WebhookSubscription.objects.create(
            organization=org,
            team=team,
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
        notif = Notification.objects.filter(
            guid=str(input.id), user_id=tenant.actor_user_id
        ).first()
        if notif is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "notification not found")
        notif.read_at = timezone.now()
        notif.save(update_fields=["read_at", "updated_at", "version"])
        return gql_success(notification_to_type(notif))
