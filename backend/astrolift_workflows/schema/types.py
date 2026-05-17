"""GraphQL types for the Temporal workflow viewer (#437)."""

from __future__ import annotations

import strawberry

JSON = strawberry.scalars.JSON


@strawberry.type(name="AstroliftWorkflowInstance")
class WorkflowInstanceType:
    """One Temporal workflow execution as the viewer shows it.

    Source-of-truth fields come straight from the Temporal visibility
    API; ``duration_seconds`` is null for in-flight runs so the UI can
    differentiate from a real zero-duration completion."""

    workflow_id: str
    workflow_type: str
    run_id: str
    status: str
    """Temporal ExecutionStatus name — RUNNING / COMPLETED / FAILED /
    CANCELED / TERMINATED / TIMED_OUT / CONTINUED_AS_NEW."""

    started_at: str
    """ISO-8601; empty string when missing."""

    closed_at: str
    """ISO-8601; empty string while running."""

    duration_seconds: float | None
    task_queue: str
    triggered_by: str
    """Best-effort actor label resolved from the platform's WorkflowRun
    mirror when the id matches; empty string when unknown."""


@strawberry.type(name="AstroliftWorkflowInstancePage")
class WorkflowInstancePageType:
    """Bounded slice of workflow instances. ``next_cursor`` is reserved
    for a future Temporal cursor-paged read; today we cap by ``limit``
    and return null."""

    items: list[WorkflowInstanceType]
    next_cursor: str | None


@strawberry.type(name="AstroliftWorkflowHistoryEvent")
class WorkflowHistoryEventType:
    """One pre-shaped row in the workflow's activity feed.

    ``payload`` carries a compact preview (activity name, failure
    message, etc.) — the dense Temporal protobuf is folded down so the
    UI doesn't need to know the event-attribute shape per kind. Power
    users can still inspect the raw event via the Temporal UI link the
    drill-down sheet renders."""

    event_type: str
    """e.g. ``EVENT_TYPE_ACTIVITY_TASK_COMPLETED``."""

    timestamp: str
    payload: JSON
    retry_count: int
    decision: str
    """``""`` | ``completed`` | ``failed`` | ``timed_out`` |
    ``cancelled`` — derived from the event kind."""


@strawberry.type(name="AstroliftWorkflowInstanceDetail")
class WorkflowInstanceDetailType:
    """Full drill-down: the instance summary plus its activity feed."""

    instance: WorkflowInstanceType
    history: list[WorkflowHistoryEventType]


def instance_to_type(row: dict, triggered_by: str = "") -> WorkflowInstanceType:
    return WorkflowInstanceType(
        workflow_id=row.get("workflow_id", "") or "",
        workflow_type=row.get("workflow_type", "") or "",
        run_id=row.get("run_id", "") or "",
        status=row.get("status", "UNKNOWN") or "UNKNOWN",
        started_at=row.get("started_at", "") or "",
        closed_at=row.get("closed_at", "") or "",
        duration_seconds=row.get("duration_seconds"),
        task_queue=row.get("task_queue", "") or "",
        triggered_by=triggered_by,
    )


def history_event_to_type(row: dict) -> WorkflowHistoryEventType:
    return WorkflowHistoryEventType(
        event_type=row.get("event_type", "") or "",
        timestamp=row.get("timestamp", "") or "",
        payload=row.get("payload") or {},
        retry_count=int(row.get("retry_count", 0) or 0),
        decision=row.get("decision", "") or "",
    )
