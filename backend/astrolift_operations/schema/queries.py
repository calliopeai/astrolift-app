"""Read-only queries for the operations app."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_operations.models import (
    AuditEvent,
    Event,
    Notification,
    WebhookSubscription,
    WorkflowRun,
)
from astrolift_operations.schema.types import (
    AuditEventType,
    EventType,
    NotificationType,
    WebhookSubscriptionType,
    WorkflowRunType,
    audit_to_type,
    event_to_type,
    notification_to_type,
    webhook_to_type,
    workflow_run_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class OperationsQuery:
    @strawberry.field
    @require_permission(Permission.AUDIT_LOG_READ)
    @tenant_scoped()
    def astrolift_events(
        self,
        info: Info,
        limit: int = 100,
        event_type: str | None = None,
    ) -> list[EventType]:
        qs = Event.objects.order_by("-occurred_at")
        if event_type:
            qs = qs.filter(event_type=event_type)
        return [event_to_type(e) for e in qs[: max(1, min(limit, 500))]]

    @strawberry.field
    @require_permission(Permission.AUDIT_LOG_READ)
    @tenant_scoped()
    def astrolift_audit_events(
        self,
        info: Info,
        limit: int = 100,
        action: str | None = None,
        decision: str | None = None,
    ) -> list[AuditEventType]:
        qs = AuditEvent.objects.order_by("-occurred_at")
        if action:
            qs = qs.filter(action=action)
        if decision:
            qs = qs.filter(decision=decision.upper())
        return [audit_to_type(a) for a in qs[: max(1, min(limit, 500))]]

    @strawberry.field
    @require_permission(Permission.AUDIT_LOG_READ)
    @tenant_scoped()
    def astrolift_workflow_runs(
        self,
        info: Info,
        limit: int = 50,
    ) -> list[WorkflowRunType]:
        qs = WorkflowRun.objects.order_by("-started_at")[: max(1, min(limit, 200))]
        return [workflow_run_to_type(w) for w in qs]

    @strawberry.field
    @require_permission(Permission.WEBHOOK_CREATE)
    @tenant_scoped()
    def astrolift_webhook_subscriptions(self, info: Info) -> list[WebhookSubscriptionType]:
        qs = WebhookSubscription.objects.order_by("-created_at")[:200]
        return [webhook_to_type(w) for w in qs]

    @strawberry.field
    @tenant_scoped()
    def astrolift_my_notifications(
        self,
        info: Info,
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[NotificationType]:
        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            return []
        qs = Notification.objects.filter(user_id=tenant.actor_user_id).order_by(
            "-created_at"
        )
        if unread_only:
            qs = qs.filter(read_at__isnull=True)
        return [notification_to_type(n) for n in qs[: max(1, min(limit, 200))]]
