"""Read queries for the Temporal workflow viewer (#437) + the configured-
Workflow surface (spec 40 §6, #968)."""

from __future__ import annotations

import strawberry
from django.db.models import Value
from django.db.models.functions import Coalesce
from strawberry.types import Info

from astrolift_graphql import KeysetPage, PageType, keyset_page, search_q
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


def _triggered_by_for(workflow_id: str, org_id: int | None = None) -> str:
    """Best-effort actor label from the WorkflowRun mirror.

    Temporal doesn't carry user identity in its visibility records, but
    the platform stamps a ``WorkflowRun`` row alongside each Temporal
    start with the triggering user. Falls back to a token-kind label
    when a service token initiated the run, then empty for unknown.

    ``org_id`` scopes the mirror lookup so a per-org viewer can't read a
    foreign org's actor identity (#1183); a fleet-wide (elevated) caller
    passes ``None`` to resolve the label across orgs."""
    qs = WorkflowRun.objects.filter(workflow_id=workflow_id)
    if org_id is not None:
        qs = qs.filter(organization_id=org_id)
    run = qs.select_related("trigger_actor_user").order_by("-started_at").first()
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


def _viewer_scope() -> tuple[bool, int | None]:
    """Access scope for the Temporal viewer reads (#1183).

    Mirrors the write gate in ``mutations._gate_instance_op``: the elevated
    platform-operator pair (``AUDIT_LOG_READ`` + ``ADMIN_ELEVATE``) sees
    every run fleet-wide; any other ``AUDIT_LOG_READ`` holder — a per-org
    permission — is scoped to runs their own org owns. Returns
    ``(elevated, caller_org_pk)``; ``caller_org_pk`` is ``None`` only for a
    non-elevated caller with no resolved org (fail closed to no rows)."""
    from astrolift_workflows.schema.mutations import _has_elevated_viewer_access

    if _has_elevated_viewer_access():
        return True, None
    return False, _caller_org_pk()


def _viewer_can_see(workflow_id: str, *, elevated: bool, caller: int | None) -> bool:
    """Read-side ownership check paired with :func:`_viewer_scope`.

    Reuses the write gate's ownership resolution
    (``mutations._run_owner_org_id`` — tier-3 ``WorkflowInstance`` then the
    ops ``WorkflowRun`` mirror) so reads and writes agree on who owns a run.
    A legacy org-less run (no mirror org) is visible only to the elevated
    pair; a scoped viewer sees nothing for it."""
    if elevated:
        return True
    if caller is None:
        return False
    from astrolift_workflows.schema.mutations import _run_owner_org_id

    return _run_owner_org_id(workflow_id) == caller


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
        ``status`` (Temporal ExecutionStatus name). ``after`` is reserved
        for future Temporal cursor support; today the resolver caps by
        limit and returns ``next_cursor = null``.

        Scoped to the caller's org (#1183): a non-elevated
        ``AUDIT_LOG_READ`` holder sees only runs their own org owns; the
        elevated platform-operator pair sees the whole namespace. The
        namespace list is fetched then filtered, so a scoped page can
        return fewer than ``limit`` rows.

        Returns an empty page when Temporal is disabled — the UI's
        empty state copy handles "no temporal" and "no runs"
        indistinguishably."""
        del after  # reserved for future cursor wiring
        elevated, caller = _viewer_scope()
        if not elevated and caller is None:
            return WorkflowInstancePageType(items=[], next_cursor=None)
        rows = list_workflow_instances(
            workflow_type=workflow_type,
            status=status,
            limit=limit,
        )
        items = []
        for r in rows:
            wid = r.get("workflow_id", "") or ""
            if not _viewer_can_see(wid, elevated=elevated, caller=caller):
                continue
            items.append(instance_to_type(r, _triggered_by_for(wid, None if elevated else caller)))
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
        Temporal disabled).

        Ownership-gated (#1183): a workflow the caller's org doesn't own
        answers ``None`` — the same as a nonexistent id — so an
        ``AUDIT_LOG_READ`` holder can't read another org's full Temporal
        history payload. The elevated platform-operator pair sees any run."""
        if not workflow_id:
            return None
        elevated, caller = _viewer_scope()
        if not _viewer_can_see(workflow_id, elevated=elevated, caller=caller):
            return None
        row = describe_workflow_instance(workflow_id)
        if row is None:
            return None
        history_rows = workflow_history(workflow_id)
        return WorkflowInstanceDetailType(
            instance=instance_to_type(row, _triggered_by_for(workflow_id, None if elevated else caller)),
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
        while a long-running workflow is still in flight.

        Ownership-gated (#1183) exactly like
        :meth:`astrolift_workflow_instance_detail`: a run the caller's org
        doesn't own answers ``None``; the elevated pair sees any run."""
        if not workflow_id:
            return None
        elevated, caller = _viewer_scope()
        if not _viewer_can_see(workflow_id, elevated=elevated, caller=caller):
            return None
        row = describe_workflow_instance(workflow_id)
        if row is None:
            return None
        return instance_to_type(row, _triggered_by_for(workflow_id, None if elevated else caller))


@strawberry.type
class WorkflowsQuery:
    """Configured-Workflow read surface (spec 40 §6). Every resolver is
    ``WORKFLOW_READ``-gated + ``@tenant_scoped`` and applies the caller's
    org filter in the body (#1042 — the decorator only asserts a context
    exists; the org match is the actual scoping)."""

    def _workflows_qs(self, *, org_pk: int | None, search: str | None = None):
        """Filtered, unordered tier-2 ``Workflow`` stream for one org.

        Shared by the list field and its paginated sibling so the two can
        never disagree about what a Workflow row is. Ordering is
        deliberately not applied here — ``keyset_page`` imposes it from
        the seek key.

        ``org_pk`` is the caller's own org pk (already confirmed against a
        supplied ``org_id`` guid by :func:`_org_pk_matches`). ``None``
        matches no rows: ``Workflow.organization`` is a non-null FK, so
        ``organization_id=None`` is an ``IS NULL`` that can never hit
        (#1042 deny-by-default).
        """
        from workflows.models import Workflow

        qs = Workflow.objects.filter(organization_id=org_pk, deleted_at__isnull=True).select_related(
            "definition", "organization"
        )
        if search:
            qs = qs.filter(
                search_q(
                    search,
                    "name",
                    "slug",
                    "description",
                    "definition__name",
                    "definition__slug",
                )
            )
        return qs

    @strawberry.field(
        description="List the org's configured Workflows (tier 2).",
        deprecation_reason=(
            "Unbounded — returns every Workflow the org owns in one response. Use workflowsPage."
        ),
    )
    @require_permission(Permission.WORKFLOW_READ)
    @tenant_scoped()
    def workflows(self, info: Info, org_id: strawberry.ID | None = None) -> list[ConfiguredWorkflowType]:
        caller, ok = _org_pk_matches(org_id)
        if not ok:
            return []
        qs = self._workflows_qs(org_pk=caller).order_by("-created_at")
        return [workflow_to_type(w) for w in qs]

    @strawberry.field(description="Cursor-paginated page of the org's configured Workflows (tier 2).")
    @require_permission(Permission.WORKFLOW_READ)
    @tenant_scoped()
    def workflows_page(
        self,
        info: Info,
        org_id: strawberry.ID | None = None,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[ConfiguredWorkflowType]:
        """Cursor-paginated ``workflows`` (#1235).

        Seek key is ``(-created_at, -guid)`` — newest first, same order
        the list field served. ``search`` matches the Workflow's own name /
        slug / description and the name / slug of the definition it
        applies, which is how an operator hunts a workflow they only half
        remember.
        """
        caller, ok = _org_pk_matches(org_id)
        if not ok:
            return KeysetPage.empty().map(workflow_to_type)
        page = keyset_page(
            self._workflows_qs(org_pk=caller, search=search),
            cursor=after,
            limit=limit,
        )
        return page.map(workflow_to_type)

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

    def _workflow_definitions_qs(self, *, org_pk: int | None, search: str | None = None):
        """Filtered, unordered tier-1 definition catalogue for one org.

        The read scope is spec 40 §2.1: the org's own definitions UNION
        every platform-global (null-org) template, via the blessed
        ``visible_to_org`` base. Shared by the list field and its
        paginated sibling; ordering is left to the caller / to
        ``keyset_page``.

        ``org_pk`` is the caller's own org pk (already confirmed against a
        supplied ``org_id`` guid by :func:`_org_pk_matches`). ``None``
        matches nothing — note this one cannot rely on an ``IS NULL``
        never hitting: ``visible_to_org(None)`` would return every
        platform-global template, so the empty case is explicit (#1042
        deny-by-default).
        """
        from workflows.models import WorkflowDefinition

        if org_pk is None:
            return WorkflowDefinition.objects.none()
        qs = (
            WorkflowDefinition.visible_to_org(org_pk)
            .filter(deleted_at__isnull=True)
            .select_related("organization")
        )
        if search:
            qs = qs.filter(search_q(search, "name", "slug", "description"))
        return qs

    @strawberry.field(
        description="Workflow definitions visible to the caller: their org's UNION all platform-global (spec 40 §2.1).",
        deprecation_reason=(
            "Unbounded — returns every visible definition in one response. Use workflowDefinitionsPage."
        ),
    )
    @require_permission(Permission.WORKFLOW_READ)
    @tenant_scoped()
    def workflow_definitions(
        self, info: Info, org_id: strawberry.ID | None = None
    ) -> list[WorkflowDefinitionSummaryType]:
        caller, ok = _org_pk_matches(org_id)
        if not ok:
            return []
        qs = self._workflow_definitions_qs(org_pk=caller).order_by("organization_id", "name")
        return [definition_summary(d) for d in qs]

    @strawberry.field(
        description=(
            "Cursor-paginated page of the workflow definitions visible to the caller, by name (A→Z)."
        )
    )
    @require_permission(Permission.WORKFLOW_READ)
    @tenant_scoped()
    def workflow_definitions_page(
        self,
        info: Info,
        org_id: strawberry.ID | None = None,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[WorkflowDefinitionSummaryType]:
        """Cursor-paginated ``workflowDefinitions`` (#1235).

        Seek key is ``(name, guid)`` ascending, which is a deliberate
        ordering change from the list field's ``(organization_id, name)``:
        ``organization_id`` is NULL on every platform-global template, and
        a NULL in the sort key makes the rows behind it unreachable — the
        walk stops there. Name-ascending is also what a catalogue wants,
        and it drops the list field's org/global grouping (``is_global``
        is on every row, so the client can still group).

        ``name`` itself is nullable on this model, so the walk sorts on a
        coalesced copy rather than the column: an unnamed definition sorts
        first instead of falling off the end of the walk.
        """
        caller, ok = _org_pk_matches(org_id)
        if not ok:
            return KeysetPage.empty().map(definition_summary)
        page = keyset_page(
            self._workflow_definitions_qs(org_pk=caller, search=search).annotate(
                sort_name=Coalesce("name", Value(""))
            ),
            cursor=after,
            limit=limit,
            sort_field="sort_name",
            tiebreak_field="guid",
            descending=False,
        )
        return page.map(definition_summary)

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
