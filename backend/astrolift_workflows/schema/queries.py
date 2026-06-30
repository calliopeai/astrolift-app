"""Read queries for the Temporal workflow viewer (#437) + the configured-
Workflow surface (spec 40 §6, #968)."""

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
from astrolift_workflows.schema.workflow_config_types import (
    ConfiguredWorkflowType,
    WorkflowDefinitionSummaryType,
    WorkflowRunType,
    definition_summary,
    run_to_type,
    workflow_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


def _caller_org_pk() -> int | None:
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def _org_pk_matches(org_id: str | None) -> tuple[int | None, bool]:
    """Resolve the caller's org and confirm an ``org_id`` argument (a guid)
    refers to it. Returns ``(caller_org_pk, ok)``. Deny-by-default (#1042):
    a mismatch is a hard ``ok=False`` so a resolver returns nothing rather
    than another org's rows. ``org_id=None`` defers to the tenant context."""
    caller = _caller_org_pk()
    if caller is None:
        return None, False
    if org_id is None:
        return caller, True
    from astrolift_identity.models import Organization

    org = Organization.objects.filter(guid=str(org_id), deleted_at__isnull=True).first()
    if org is None or org.pk != caller:
        return caller, False
    return caller, True


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


@strawberry.type
class WorkflowsQuery:
    """Configured-Workflow read surface (spec 40 §6). Every resolver is
    ``WORKFLOW_READ``-gated + ``@tenant_scoped`` and applies the caller's
    org filter in the body (#1042 — the decorator only asserts a context
    exists; the org match is the actual scoping)."""

    @strawberry.field(description="List the org's configured Workflows (tier 2).")
    @require_permission(Permission.WORKFLOW_READ)
    @tenant_scoped()
    def workflows(self, info: Info, org_id: strawberry.ID | None = None) -> list[ConfiguredWorkflowType]:
        caller, ok = _org_pk_matches(org_id)
        if not ok:
            return []
        from workflows.models import Workflow

        qs = (
            Workflow.objects.filter(organization_id=caller, deleted_at__isnull=True)
            .select_related("definition", "organization")
            .order_by("-created_at")
        )
        return [workflow_to_type(w) for w in qs]

    @strawberry.field(description="One configured Workflow by slug, with its recent runs.")
    @require_permission(Permission.WORKFLOW_READ)
    @tenant_scoped()
    def workflow(
        self, info: Info, slug: str, org_id: strawberry.ID | None = None
    ) -> ConfiguredWorkflowType | None:
        caller, ok = _org_pk_matches(org_id)
        if not ok:
            return None
        from workflows.models import Workflow

        wf = (
            Workflow.objects.filter(organization_id=caller, slug=slug, deleted_at__isnull=True)
            .select_related("definition", "organization")
            .first()
        )
        if wf is None:
            return None
        return workflow_to_type(wf, with_runs=True)

    @strawberry.field(
        description="Workflow definitions visible to the caller: their org's UNION all platform-global (spec 40 §2.1)."
    )
    @require_permission(Permission.WORKFLOW_READ)
    @tenant_scoped()
    def workflow_definitions(
        self, info: Info, org_id: strawberry.ID | None = None
    ) -> list[WorkflowDefinitionSummaryType]:
        caller, ok = _org_pk_matches(org_id)
        if not ok:
            return []
        from workflows.models import WorkflowDefinition

        qs = (
            WorkflowDefinition.visible_to_org(caller)
            .filter(deleted_at__isnull=True)
            .select_related("organization")
            .order_by("organization_id", "name")
        )
        return [definition_summary(d) for d in qs]

    @strawberry.field(
        description="One visible workflow definition by slug (prefers the org's over a global)."
    )
    @require_permission(Permission.WORKFLOW_READ)
    @tenant_scoped()
    def workflow_definition(
        self, info: Info, slug: str, org_id: strawberry.ID | None = None
    ) -> WorkflowDefinitionSummaryType | None:
        caller, ok = _org_pk_matches(org_id)
        if not ok:
            return None
        from workflows.models import WorkflowDefinition

        visible = (
            WorkflowDefinition.visible_to_org(caller)
            .filter(slug=slug, deleted_at__isnull=True)
            .select_related("organization")
        )
        d = visible.filter(organization_id=caller).first() or visible.first()
        return definition_summary(d) if d else None

    @strawberry.field(description="Runs (tier 3) of one configured Workflow, newest first.")
    @require_permission(Permission.WORKFLOW_READ)
    @tenant_scoped()
    def workflow_runs(
        self, info: Info, workflow_id: strawberry.ID, org_id: strawberry.ID | None = None
    ) -> list[WorkflowRunType]:
        caller, ok = _org_pk_matches(org_id)
        if not ok:
            return []
        from workflows.models import Workflow, WorkflowInstance

        wf = Workflow.objects.filter(
            guid=str(workflow_id), organization_id=caller, deleted_at__isnull=True
        ).first()
        if wf is None:
            return []
        runs = WorkflowInstance.objects.filter(
            configured_workflow=wf, organization_id=caller, deleted_at__isnull=True
        ).order_by("-started_at")[:100]
        return [run_to_type(r) for r in runs]
