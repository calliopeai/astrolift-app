"""Read-only queries for the operations app."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_operations.models import (
    AuditEvent,
    Event,
    WorkflowRun,
)
from astrolift_operations.schema.types import (
    AuditEventType,
    EventType,
    WorkflowRunType,
    audit_to_type,
    event_to_type,
    workflow_run_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission


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
