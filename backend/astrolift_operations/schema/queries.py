"""Read-only queries for the operations app."""

from __future__ import annotations

import base64
import binascii
import json

import strawberry
from strawberry.types import Info

from astrolift_operations.models import (
    AlertEvent,
    AlertRule,
    AuditEvent,
    Event,
    Notification,
    WebhookSubscription,
    WorkflowRun,
)
from astrolift_operations.schema.types import (
    AlertEventType,
    AlertRuleType,
    AuditEventType,
    EventPageType,
    EventType,
    NotificationType,
    WebhookSubscriptionType,
    WorkflowRunType,
    alert_event_to_type,
    alert_rule_to_type,
    audit_to_type,
    event_to_type,
    notification_to_type,
    webhook_to_type,
    workflow_run_to_type,
)
from astrolift_graphql import GUID
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
    def astrolift_events_page(
        self,
        info: Info,
        limit: int = 100,
        after: str | None = None,
        event_type: str | None = None,
    ) -> EventPageType:
        """Cursor-paginated event stream.

        ``after`` is the opaque cursor returned by the previous page;
        omit it to start from the newest event. Cursor is base64-JSON
        of ``[occurred_at_iso, guid_str]`` so the (occurred_at, guid)
        composite is the seek key — guid is a UUIDv7 so the secondary
        sort is also time-ordered, eliminating tie-break churn.
        """
        page_size = max(1, min(limit, 500))
        qs = Event.objects.order_by("-occurred_at", "-guid")
        if event_type:
            qs = qs.filter(event_type=event_type)
        if after:
            decoded = _decode_event_cursor(after)
            if decoded is not None:
                from django.db.models import Q

                cursor_at, cursor_guid = decoded
                qs = qs.filter(
                    Q(occurred_at__lt=cursor_at)
                    | (Q(occurred_at=cursor_at) & Q(guid__lt=cursor_guid))
                )
        # Fetch one extra to detect end-of-stream cheaply.
        rows = list(qs[: page_size + 1])
        items = rows[:page_size]
        next_cursor = (
            _encode_event_cursor(items[-1].occurred_at, str(items[-1].guid))
            if len(rows) > page_size and items
            else None
        )
        return EventPageType(
            items=[event_to_type(e) for e in items],
            next_cursor=next_cursor,
        )

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
    def astrolift_webhook_subscriptions(
        self,
        info: Info,
        app_slug: str | None = None,
    ) -> list[WebhookSubscriptionType]:
        """List webhook subscriptions.

        Without ``app_slug``: org-wide subscriptions (those not bound
        to any app). Pass ``app_slug`` to list per-app subscriptions
        scoped to that app's UI page (#281)."""
        qs = WebhookSubscription.objects.order_by("-created_at")
        if app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        else:
            qs = qs.filter(registered_app__isnull=True)
        return [webhook_to_type(w) for w in qs[:200]]

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
        qs = Notification.objects.filter(user_id=tenant.actor_user_id).order_by("-created_at")
        if unread_only:
            qs = qs.filter(read_at__isnull=True)
        return [notification_to_type(n) for n in qs[: max(1, min(limit, 200))]]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_alert_rules(
        self,
        info: Info,
        target: str | None = None,
        target_id: str | None = None,
        active_only: bool = True,
    ) -> list[AlertRuleType]:
        """List alert rules.

        Without ``target``: every rule visible to the tenant. Pass
        ``target=app|env|workload|global`` (and optionally
        ``target_id``) to scope to one target."""
        qs = AlertRule.objects.select_related("organization")
        if active_only:
            qs = qs.filter(is_active=True)
        if target:
            qs = qs.filter(target=target)
        if target_id:
            qs = qs.filter(target_id=target_id)
        qs = qs.order_by("-created_at")[:200]
        return [alert_rule_to_type(r) for r in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_alert_events(
        self,
        info: Info,
        rule_id: GUID | None = None,
        unresolved_only: bool = False,
        limit: int = 100,
    ) -> list[AlertEventType]:
        qs = AlertEvent.objects.select_related("rule")
        if rule_id is not None:
            qs = qs.filter(rule__guid=str(rule_id))
        if unresolved_only:
            qs = qs.filter(resolved_at__isnull=True)
        qs = qs.order_by("-fired_at")[: max(1, min(limit, 500))]
        return [alert_event_to_type(e) for e in qs]


# ---------------------------------------------------------------------------
# Cursor helpers (Event)
# ---------------------------------------------------------------------------


def _encode_event_cursor(occurred_at, guid: str) -> str:
    payload = json.dumps([occurred_at.isoformat(), guid], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode()


def _decode_event_cursor(token: str):
    """Return ``(occurred_at_dt, guid_str)`` or ``None`` if the token
    is malformed. We swallow garbage so a bogus cursor restarts from
    the top instead of erroring — UX over strictness."""
    import datetime as dt

    pad = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + pad)
        ts, guid = json.loads(raw)
        return dt.datetime.fromisoformat(ts), guid
    except (binascii.Error, ValueError, TypeError, json.JSONDecodeError):
        return None
