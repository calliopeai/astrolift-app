"""Read queries for the Temporal workflow viewer (#437)."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_operations.models import WorkflowRun
from astrolift_workflows.client import (
    describe_workflow_instance,
    list_workflow_instances,
    workflow_history,
)
from astrolift_workflows.schema.types import (
    WorkflowInstanceDetailType,
    WorkflowInstancePageType,
    WorkflowInstanceType,
    history_event_to_type,
    instance_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission


def _triggered_by_for(workflow_id: str) -> str:
    """Best-effort actor label from the WorkflowRun mirror.

    Temporal doesn't carry user identity in its visibility records, but
    the platform stamps a ``WorkflowRun`` row alongside each Temporal
    start with the triggering user. Falls back to a token-kind label
    when a service token initiated the run, then empty for unknown."""
    run = (
        WorkflowRun.objects.filter(workflow_id=workflow_id)
        .select_related("trigger_actor_user")
        .order_by("-started_at")
        .first()
    )
    if run is None:
        return ""
    user = run.trigger_actor_user
    if user is not None:
        first = (user.first_name or "").strip()
        last = (user.last_name or "").strip()
        full = (first + " " + last).strip()
        if full:
            return full
        if user.email:
            return user.email
        if user.username:
            return user.username
    if run.trigger_actor_token_kind:
        return f"token:{run.trigger_actor_token_kind}"
    return ""


@strawberry.type
class TemporalWorkflowsQuery:
    @strawberry.field
    @require_permission(Permission.AUDIT_LOG_READ)
    @tenant_scoped()
    def astrolift_workflow_instances(
        self,
        info: Info,
        workflow_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> WorkflowInstancePageType:
        """List recent Temporal instances.

        Filters: ``workflow_type`` (e.g. ``DeployAppWorkflow``),
        ``status`` (Temporal ExecutionStatus name). Defaults return
        the most recent 50 across the namespace. ``after`` is reserved
        for future Temporal cursor support; today the resolver caps by
        limit and returns ``next_cursor = null``.

        Returns an empty page when Temporal is disabled — the UI's
        empty state copy handles "no temporal" and "no runs"
        indistinguishably."""
        del after  # reserved for future cursor wiring
        rows = list_workflow_instances(
            workflow_type=workflow_type,
            status=status,
            limit=limit,
        )
        items = [instance_to_type(r, _triggered_by_for(r.get("workflow_id", "") or "")) for r in rows]
        return WorkflowInstancePageType(items=items, next_cursor=None)

    @strawberry.field
    @require_permission(Permission.AUDIT_LOG_READ)
    @tenant_scoped()
    def astrolift_workflow_instance_detail(
        self,
        info: Info,
        workflow_id: str,
    ) -> WorkflowInstanceDetailType | None:
        """Full drill-down for one workflow execution — summary plus
        pre-shaped activity feed. ``None`` when the instance can't be
        found (typo, expired from Temporal's history window, or
        Temporal disabled)."""
        if not workflow_id:
            return None
        row = describe_workflow_instance(workflow_id)
        if row is None:
            return None
        history_rows = workflow_history(workflow_id)
        return WorkflowInstanceDetailType(
            instance=instance_to_type(row, _triggered_by_for(workflow_id)),
            history=[history_event_to_type(h) for h in history_rows],
        )

    @strawberry.field
    @require_permission(Permission.AUDIT_LOG_READ)
    @tenant_scoped()
    def astrolift_workflow_instance(
        self,
        info: Info,
        workflow_id: str,
    ) -> WorkflowInstanceType | None:
        """Single-instance summary without the history feed — cheap
        polling endpoint for the status pill on the drill-down sheet
        while a long-running workflow is still in flight."""
        if not workflow_id:
            return None
        row = describe_workflow_instance(workflow_id)
        if row is None:
            return None
        return instance_to_type(row, _triggered_by_for(workflow_id))
