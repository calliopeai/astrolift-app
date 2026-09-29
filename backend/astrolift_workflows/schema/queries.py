"""Read queries for the Temporal workflow viewer (#437) + the configured-
Workflow surface (spec 40 §6, #968)."""

from __future__ import annotations

import uuid

import strawberry
from django.core.exceptions import ValidationError
from django.db.models import Count, Prefetch, Q, Value
from django.db.models.functions import Coalesce
from strawberry.types import Info

from astrolift_graphql import (
    FilterField,
    KeysetPage,
    PageType,
    UnsupportedSort,
    filter_q,
    filter_values,
    keyset_page,
    parse_sort_spec,
    search_q,
)
from astrolift_operations.models import WorkflowRun
from astrolift_workflows.client import (
    describe_workflow_instance,
    list_workflow_instances,
    workflow_history,
)
from astrolift_workflows.schema.execution_types import WorkflowExecutionStages, WorkflowExecutionType
from astrolift_workflows.schema.types import (
    WorkflowInstanceDetailType,
    WorkflowInstancePageType,
    WorkflowInstanceType,
    history_event_to_type,
    instance_to_type,
)
from astrolift_workflows.schema.workflow_config_types import (
    ConfiguredWorkflowType,
    PendingHumanGateType,
    WorkflowDefinitionRunsFilterInput,
    WorkflowDefinitionRunType,
    WorkflowDefinitionSummaryType,
    WorkflowRunType,
    definition_run_to_type,
    definition_summary,
    environment_model_map,
    pending_gate_to_type,
    run_to_type,
    workflow_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import (
    Permission,
    PermissionDenied,
    check_permission,
    granted_scopes,
    require_permission,
)
from core.tenancy import get_current_tenant
from workflows.scopes import (
    covered_project_ids,
    definition_scope_by_slug,
    execution_scope_by_id,
    may_decide_human_gate,
    visible_runs,
    workflow_run_scope,
    workflow_run_scope_by_id,
    workflow_scope_by_guid,
    workflow_scope_by_slug,
)


def _viewer_id() -> int | None:
    tenant = get_current_tenant()
    return tenant.actor_user_id if tenant else None


def _definition_runs_qs(caller: int):
    """The definition runs the caller may read, with what a row renders joined."""
    qs = (
        WorkflowRun.objects.filter(
            organization_id=caller,
            workflow_kind="WorkflowDefinitionRunWorkflow",
            workflow_definition_id__isnull=False,
            workflow_definition__deleted_at__isnull=True,
        )
        .select_related(
            "workflow_definition",
            "workflow_definition__project",
            "current_stage_execution",
            "current_stage_execution__stage",
            "parent_run",
            "parent_stage_execution",
        )
        .annotate(child_run_count=Count("child_runs", filter=Q(child_runs__deleted_at__isnull=True)))
    )
    return visible_runs(qs, caller, Permission.WORKFLOW_READ)


def _iexact_any(path: str, values) -> Q:
    query = Q(pk__in=[])
    for value in values:
        query |= Q(**{f"{path}__iexact": value})
    return query


def _user_ids(values, viewer_id: int | None) -> list[int]:
    out: list[int] = []
    for value in values:
        if value == "me":
            if viewer_id is not None:
                out.append(viewer_id)
            continue
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


_DEFINITION_RUN_FILTERS: dict[str, FilterField] = {
    "status": FilterField("status"),
    "trigger": FilterField("trigger_kind"),
    "definition": FilterField(q=lambda v: _iexact_any("workflow_definition__slug", v)),
    "project": FilterField(q=lambda v: _iexact_any("workflow_definition__project__slug", v)),
    # ``started_by`` / ``started_by_me`` need the viewer; applied below.
}


def _apply_definition_run_filter(qs, values: dict, viewer_id: int | None):
    qs = qs.filter(filter_q(values, _DEFINITION_RUN_FILTERS))
    if "started_by" in values:
        qs = qs.filter(trigger_actor_user_id__in=_user_ids(values["started_by"], viewer_id))
    mine = values.get("started_by_me")
    if mine is True:
        qs = qs.filter(trigger_actor_user_id=viewer_id) if viewer_id is not None else qs.none()
    elif mine is False and viewer_id is not None:
        qs = qs.filter(Q(trigger_actor_user_id__isnull=True) | ~Q(trigger_actor_user_id=viewer_id))
    return qs


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
    return _triggered_by_map([workflow_id], org_id).get(workflow_id, "")


def _triggered_by_map(workflow_ids: list[str], org_id: int | None = None) -> dict[str, str]:
    """:func:`_triggered_by_for` for a whole page in one query (#1983)."""
    qs = WorkflowRun.objects.filter(workflow_id__in=[wid for wid in workflow_ids if wid])
    if org_id is not None:
        qs = qs.filter(organization_id=org_id)
    latest: dict[str, WorkflowRun] = {}
    for run in qs.select_related("trigger_actor_user").order_by("-started_at"):
        latest.setdefault(run.workflow_id, run)
    return {wid: _actor_label(run) for wid, run in latest.items()}


def _actor_label(run) -> str:
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


def _viewer_scope(user) -> tuple[bool, int | None]:
    """Access scope for the Temporal viewer reads (#1183).

    Mirrors the write gate in ``mutations._gate_instance_op``: the platform
    operator sees every run fleet-wide; any other ``AUDIT_LOG_READ`` holder
    is scoped to runs their own org owns, at each run's own scope (#1965).
    Returns ``(elevated, caller_org_pk)``; ``caller_org_pk`` is ``None``
    only for a non-elevated caller with no resolved org (fail closed to no
    rows)."""
    from astrolift_workflows.schema.mutations import _has_elevated_viewer_access

    if _has_elevated_viewer_access(user):
        return True, None
    return False, _caller_org_pk()


def _viewer_can_see(workflow_id: str, *, elevated: bool, caller: int | None, org_wide: bool = True) -> bool:
    """Read-side ownership check paired with :func:`_viewer_scope`.

    Reuses the write gate's ownership resolution
    (``mutations._run_owner_org_id`` — tier-3 ``WorkflowInstance`` then the
    ops ``WorkflowRun`` mirror) so reads and writes agree on who owns a run.
    A legacy org-less run (no mirror org) is visible only to the platform
    operator; a scoped viewer sees nothing for it. A caller whose
    ``AUDIT_LOG_READ`` is not org-wide (``org_wide=False``) also needs it at
    the run's own scope; the single-run readers check that in their gate."""
    if elevated:
        return True
    if caller is None:
        return False
    from astrolift_workflows.schema.mutations import _run_owner_org_id

    if _run_owner_org_id(workflow_id) != caller:
        return False
    if org_wide:
        return True
    try:
        check_permission(Permission.AUDIT_LOG_READ, scope=workflow_run_scope(workflow_id, caller))
    except PermissionDenied:
        return False
    return True


def _visible_workflow_ids(
    workflow_ids: list[str], *, elevated: bool, caller: int | None, org_wide: bool
) -> set[str]:
    """The page-wide form of :func:`_viewer_can_see` (#1983), in a fixed
    number of queries instead of an ownership lookup and a permission check
    per row. Same rules: the owner is the ``WorkflowInstance`` org, else the
    newest ``WorkflowRun`` mirror's; below the org a run is visible through
    the app it records (``visible_runs``), else through its definition's
    project; an org-less legacy run only to the operator."""
    ids = {wid for wid in workflow_ids if wid}
    if elevated:
        return ids
    if caller is None or not ids:
        return set()
    from workflows.models import WorkflowInstance

    owners: dict[str, int] = {}
    for wid, org_id in (
        WorkflowRun.objects.filter(workflow_id__in=ids, organization__isnull=False, deleted_at__isnull=True)
        .order_by("pk")
        .values_list("workflow_id", "organization_id")
    ):
        owners[wid] = org_id  # ascending pk: the newest mirror wins
    for wid, org_id in WorkflowInstance.objects.filter(
        temporal_workflow_id__in=ids, organization__isnull=False, deleted_at__isnull=True
    ).values_list("temporal_workflow_id", "organization_id"):
        owners[wid] = org_id  # the tier-3 instance outranks the mirror
    owned = {wid for wid in ids if owners.get(wid) == caller}
    if org_wide or not owned:
        return owned

    runs = WorkflowRun.objects.filter(workflow_id__in=owned, organization_id=caller)
    with_run = set(runs.values_list("workflow_id", flat=True))
    visible = set(visible_runs(runs, caller, Permission.AUDIT_LOG_READ).values_list("workflow_id", flat=True))
    instance_only = owned - with_run
    if instance_only:
        projects = covered_project_ids(caller, Permission.AUDIT_LOG_READ)
        instances = WorkflowInstance.objects.filter(
            temporal_workflow_id__in=instance_only, organization_id=caller, deleted_at__isnull=True
        )
        if projects is not None:
            instances = instances.filter(workflow__project_id__in=projects)
        visible |= set(instances.values_list("temporal_workflow_id", flat=True))
    return visible


@strawberry.type
class TemporalWorkflowsQuery:
    @strawberry.field
    @require_permission(Permission.AUDIT_LOG_READ, any_scope=True)
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
        ``status`` (Temporal ExecutionStatus name). ``after`` takes the
        ``next_cursor`` of a previous page — Temporal's own opaque page
        token — and ``next_cursor`` is ``null`` on the last page (#1236).

        Scoped to the caller's org (#1183): a non-elevated
        ``AUDIT_LOG_READ`` holder sees only runs their own org owns, and a
        holder below the org sees only the runs of the apps and projects
        their grant covers (#1965); the platform operator sees the whole
        namespace. The namespace list is fetched then filtered, so a scoped
        page can return fewer than ``limit`` rows, including zero while
        ``next_cursor`` is still non-null. Callers page until the cursor
        is null rather than until a page comes back short.

        Returns an empty page when Temporal is disabled — the UI's
        empty state copy handles "no temporal" and "no runs"
        indistinguishably."""
        elevated, caller = _viewer_scope(info.context.user)
        if not elevated and caller is None:
            return WorkflowInstancePageType(items=[], next_cursor=None)
        org_wide = granted_scopes(get_current_tenant(), Permission.AUDIT_LOG_READ).org
        rows, next_cursor = list_workflow_instances(
            workflow_type=workflow_type,
            status=status,
            limit=limit,
            after=after,
        )
        visible = _visible_workflow_ids(
            [r.get("workflow_id", "") or "" for r in rows],
            elevated=elevated,
            caller=caller,
            org_wide=org_wide,
        )
        actors = _triggered_by_map(sorted(visible), None if elevated else caller)
        items = []
        for r in rows:
            wid = r.get("workflow_id", "") or ""
            if wid not in visible:
                continue
            items.append(instance_to_type(r, actors.get(wid, "")))
        return WorkflowInstancePageType(items=items, next_cursor=next_cursor)

    @strawberry.field
    @require_permission(Permission.AUDIT_LOG_READ, scope=workflow_run_scope_by_id("workflow_id"))
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
        history payload. The gate checks the run's own scope (#1965). The
        platform operator sees any run."""
        if not workflow_id:
            return None
        elevated, caller = _viewer_scope(info.context.user)
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
    @require_permission(Permission.AUDIT_LOG_READ, scope=workflow_run_scope_by_id("workflow_id"))
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
        doesn't own answers ``None``; the platform operator sees any run."""
        if not workflow_id:
            return None
        elevated, caller = _viewer_scope(info.context.user)
        if not _viewer_can_see(workflow_id, elevated=elevated, caller=caller):
            return None
        row = describe_workflow_instance(workflow_id)
        if row is None:
            return None
        return instance_to_type(row, _triggered_by_for(workflow_id, None if elevated else caller))


def _workflows_qs(*, org_pk: int | None, search: str | None = None):
    """Filtered, unordered tier-2 ``Workflow`` stream for one org.

    Shared by the list field and its paginated sibling so the two can
    never disagree about what a Workflow row is. Ordering is
    deliberately not applied here — ``keyset_page`` imposes it from
    the seek key.

    ``org_pk`` is the caller's own org pk (already confirmed against a
    supplied ``org_id`` guid by :func:`_org_pk_matches`). ``None``
    matches no rows: ``Workflow.organization`` is a non-null FK, so
    ``organization_id=None`` is an ``IS NULL`` that can never hit
    (#1042 deny-by-default). A ``WORKFLOW_READ`` grant below the org
    narrows the stream to the workflows of the projects it covers (#1965).
    """
    from workflows.models import Workflow

    qs = Workflow.objects.filter(organization_id=org_pk, deleted_at__isnull=True).select_related(
        "definition", "organization"
    )
    projects = covered_project_ids(org_pk, Permission.WORKFLOW_READ)
    if projects is not None:
        qs = qs.filter(definition__project_id__in=projects)
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


def _workflow_definitions_qs(
    *,
    org_pk: int | None,
    search: str | None = None,
    project_id: str | None = None,
):
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
    deny-by-default). A ``WORKFLOW_READ`` grant below the org narrows the
    catalogue to the definitions of the projects it covers; templates and
    project-less definitions are org-level (#1965).
    """
    from workflows.models import WorkflowDefinition, WorkflowStage

    if org_pk is None:
        return WorkflowDefinition.objects.none()
    qs = (
        WorkflowDefinition.visible_to_org(org_pk)
        .filter(deleted_at__isnull=True)
        .select_related("organization", "project", "project__team")
        .prefetch_related(
            Prefetch(
                "stages",
                queryset=WorkflowStage.objects.filter(deleted_at__isnull=True)
                .select_related("agent_definition")
                .order_by("order"),
            )
        )
    )
    projects = covered_project_ids(org_pk, Permission.WORKFLOW_READ)
    if projects is not None:
        qs = qs.filter(project_id__in=projects)
    if project_id is not None:
        try:
            qs = qs.filter(
                organization_id=org_pk,
                project__guid=str(project_id),
                project__organization_id=org_pk,
            )
        except (TypeError, ValueError, ValidationError):
            return WorkflowDefinition.objects.none()
    if search:
        qs = qs.filter(search_q(search, "name", "slug", "description"))
    return qs


@strawberry.type
class WorkflowsQuery:
    """Configured-Workflow read surface (spec 40 §6). Every resolver is
    ``WORKFLOW_READ``-gated + ``@tenant_scoped`` and applies the caller's
    org filter in the body (#1042 — the decorator only asserts a context
    exists; the org match is the actual scoping)."""

    @strawberry.field(description="Recorded stage history of an exact owned execution, newest first.")
    @require_permission(Permission.WORKFLOW_READ, scope=execution_scope_by_id())
    @tenant_scoped()
    def workflow_execution_stages(
        self,
        info: Info,
        execution_id: strawberry.ID,
        limit: int = 100,
        after: str | None = None,
    ) -> WorkflowExecutionStages | None:
        from astrolift_workflows.execution_controls import execution_stages, find_execution

        run = find_execution(_caller_org_pk(), str(execution_id))
        return WorkflowExecutionStages(**execution_stages(run, limit=limit, after=after)) if run else None

    @strawberry.field(
        description="One owned execution by the WorkflowRun ID returned on dispatch or its GUID."
    )
    @require_permission(Permission.WORKFLOW_READ, scope=execution_scope_by_id())
    @tenant_scoped()
    def workflow_execution(self, info: Info, execution_id: strawberry.ID) -> WorkflowExecutionType | None:
        from astrolift_workflows.execution_controls import execution_state, find_execution

        run = find_execution(_caller_org_pk(), str(execution_id))
        return WorkflowExecutionType(**execution_state(run)) if run is not None else None

    @strawberry.field(
        description="List the org's configured Workflows (tier 2).",
        deprecation_reason=(
            "Unbounded — returns every Workflow the org owns in one response. Use workflowsPage."
        ),
    )
    @require_permission(Permission.WORKFLOW_READ, any_scope=True)
    @tenant_scoped()
    def workflows(self, info: Info, org_id: strawberry.ID | None = None) -> list[ConfiguredWorkflowType]:
        caller, ok = _org_pk_matches(org_id)
        if not ok:
            return []
        qs = _workflows_qs(org_pk=caller).order_by("-created_at")
        return [workflow_to_type(w) for w in qs]

    @strawberry.field(description="Cursor-paginated page of the org's configured Workflows (tier 2).")
    @require_permission(Permission.WORKFLOW_READ, any_scope=True)
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
            _workflows_qs(org_pk=caller, search=search),
            cursor=after,
            limit=limit,
        )
        return page.map(workflow_to_type)

    @strawberry.field(description="One configured Workflow by slug, with its recent runs.")
    @require_permission(Permission.WORKFLOW_READ, scope=workflow_scope_by_slug("slug"))
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
        description="Workflow definitions visible to the caller: their org's UNION all platform-global (spec 40 §2.1).",
        deprecation_reason=(
            "Unbounded — returns every visible definition in one response. Use workflowDefinitionsPage."
        ),
    )
    @require_permission(Permission.WORKFLOW_READ, any_scope=True)
    @tenant_scoped()
    def workflow_definitions(
        self,
        info: Info,
        org_id: strawberry.ID | None = None,
        project_id: strawberry.ID | None = None,
    ) -> list[WorkflowDefinitionSummaryType]:
        caller, ok = _org_pk_matches(org_id)
        if not ok:
            return []
        qs = _workflow_definitions_qs(
            org_pk=caller,
            project_id=(str(project_id) if project_id is not None else None),
        ).order_by("organization_id", "name")
        definitions = list(qs)
        models = environment_model_map(definitions)
        return [definition_summary(d, environment_models=models) for d in definitions]

    @strawberry.field(
        description=(
            "Cursor-paginated page of the workflow definitions visible to the caller, by name (A→Z)."
        )
    )
    @require_permission(Permission.WORKFLOW_READ, any_scope=True)
    @tenant_scoped()
    def workflow_definitions_page(
        self,
        info: Info,
        org_id: strawberry.ID | None = None,
        search: str | None = None,
        project_id: strawberry.ID | None = None,
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
            _workflow_definitions_qs(
                org_pk=caller,
                search=search,
                project_id=(str(project_id) if project_id is not None else None),
            ).annotate(sort_name=Coalesce("name", Value(""))),
            cursor=after,
            limit=limit,
            sort_field="sort_name",
            tiebreak_field="guid",
            descending=False,
        )
        models = environment_model_map(page.rows)
        return page.map(lambda d: definition_summary(d, environment_models=models))

    @strawberry.field(
        description="One visible workflow definition by slug (prefers the org's over a global)."
    )
    @require_permission(Permission.WORKFLOW_READ, scope=definition_scope_by_slug("slug"))
    @tenant_scoped()
    def workflow_definition(
        self, info: Info, slug: str, org_id: strawberry.ID | None = None
    ) -> WorkflowDefinitionSummaryType | None:
        caller, ok = _org_pk_matches(org_id)
        if not ok:
            return None
        from workflows.models import WorkflowDefinition, WorkflowStage

        visible = (
            WorkflowDefinition.visible_to_org(caller)
            .filter(slug=slug, deleted_at__isnull=True)
            .select_related("organization", "project", "project__team")
            .prefetch_related(
                Prefetch(
                    "stages",
                    queryset=WorkflowStage.objects.filter(deleted_at__isnull=True)
                    .select_related("agent_definition")
                    .order_by("order"),
                )
            )
        )
        d = visible.filter(organization_id=caller).first() or visible.first()
        if d is None:
            return None
        return definition_summary(d, environment_models=environment_model_map([d]))

    @strawberry.field(description="Runs (tier 3) of one configured Workflow, newest first.")
    @require_permission(Permission.WORKFLOW_READ, scope=workflow_scope_by_guid("workflow_id"))
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

    @strawberry.field(description="Recent runs of workflow definitions visible in the caller's organization.")
    @require_permission(Permission.WORKFLOW_READ, any_scope=True)
    @tenant_scoped()
    def workflow_definition_runs(
        self,
        info: Info,
        org_id: strawberry.ID | None = None,
        project_id: strawberry.ID | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[WorkflowDefinitionRunType]:
        caller, ok = _org_pk_matches(org_id)
        if not ok:
            return []
        from astrolift_operations.models import WorkflowRun

        if status is not None and status not in WorkflowRun.Status.values:
            return []
        qs = (
            WorkflowRun.objects.filter(
                organization_id=caller,
                workflow_kind="WorkflowDefinitionRunWorkflow",
                workflow_definition_id__isnull=False,
                workflow_definition__deleted_at__isnull=True,
            )
            .select_related(
                "workflow_definition",
                "workflow_definition__project",
                "current_stage_execution",
                "current_stage_execution__stage",
                "parent_run",
                "parent_stage_execution",
            )
            .annotate(
                child_run_count=Count(
                    "child_runs",
                    filter=Q(child_runs__deleted_at__isnull=True),
                )
            )
        )
        qs = visible_runs(qs, caller, Permission.WORKFLOW_READ)
        if project_id is not None:
            try:
                qs = qs.filter(
                    workflow_definition__project__guid=str(project_id),
                    workflow_definition__project__organization_id=caller,
                )
            except (TypeError, ValueError, ValidationError):
                return []
        if status is not None:
            qs = qs.filter(status=status)
        rows = qs.order_by("-started_at", "-guid")[: max(1, min(int(limit), 200))]
        return [definition_run_to_type(run) for run in rows]

    @strawberry.field(
        description=(
            "Runs of workflow definitions visible in the caller's organization, cursor-paged, "
            "with search and filters (#2155)."
        )
    )
    @require_permission(Permission.WORKFLOW_READ, any_scope=True)
    @tenant_scoped()
    def workflow_definition_runs_page(
        self,
        info: Info,
        org_id: strawberry.ID | None = None,
        search: str | None = None,
        filter: WorkflowDefinitionRunsFilterInput | None = None,
        sort: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[WorkflowDefinitionRunType]:
        """The paged sibling of ``workflowDefinitionRuns``.

        ``search`` matches the definition's name and slug, the project slug,
        and the run guid and Temporal workflow id by prefix. ``filter``:
        status (any of), definition and project (slugs), trigger kind, and
        the initiator (``startedBy`` user ids or "me", and ``startedByMe``).
        ``sort`` is ``created`` in either direction, newest first by default:
        a cursor list sorts on one NOT NULL key, and ``startedAt`` is
        nullable. Same visibility as the list: the caller's org, narrowed to
        the runs whose owner the caller covers.
        """
        caller, ok = _org_pk_matches(org_id)
        if not ok:
            return KeysetPage.empty().map(definition_run_to_type)
        pairs = parse_sort_spec(sort) or [("created", True)]
        if len(pairs) > 1 or pairs[0][0] != "created":
            raise UnsupportedSort(f"sort {sort!r} is not available on workflow runs; supported: created")
        descending = pairs[0][1]
        qs = _definition_runs_qs(caller)
        if search and search.strip():
            term = search.strip()
            qs = qs.filter(
                search_q(
                    term,
                    "workflow_definition__name",
                    "workflow_definition__slug",
                    "workflow_definition__project__slug",
                    prefix=("guid", "workflow_id"),
                )
            )
        qs = _apply_definition_run_filter(qs, filter_values(filter), _viewer_id())
        page = keyset_page(
            qs,
            cursor=after,
            limit=limit,
            descending=descending,
            cursor_scope=f"workflow-definition-runs:{'-' if descending else ''}created",
        )
        return page.map(definition_run_to_type)

    @strawberry.field(description="One run of a workflow definition by guid, or null (#2155).")
    @require_permission(Permission.WORKFLOW_READ, any_scope=True)
    @tenant_scoped()
    def workflow_definition_run(
        self, info: Info, guid: str, org_id: strawberry.ID | None = None
    ) -> WorkflowDefinitionRunType | None:
        """So a run page can open a workflow run by the guid a list row carries.

        Resolved inside the caller's org and its run visibility, exactly as
        ``workflowDefinitionRuns`` lists them: another org's run, one the
        caller's grants do not cover, and a malformed guid all read as null.
        """
        caller, ok = _org_pk_matches(org_id)
        if not ok:
            return None
        try:
            run_guid = str(uuid.UUID(str(guid)))
        except (TypeError, ValueError, AttributeError):
            return None
        run = _definition_runs_qs(caller).filter(guid=run_guid, organization_id=caller).first()
        return definition_run_to_type(run) if run is not None else None

    @strawberry.field(
        description=(
            "Open human_gate stage executions across the org's runs that the caller may "
            "decide, newest first (#1820)."
        )
    )
    @require_permission(Permission.WORKFLOW_TRIGGER, any_scope=True)
    @tenant_scoped()
    def pending_human_gates(
        self,
        info: Info,
        org_id: strawberry.ID | None = None,
        limit: int = 50,
    ) -> list[PendingHumanGateType]:
        caller, ok = _org_pk_matches(org_id)
        if not ok:
            return []
        from workflows.models import WorkflowStage, WorkflowStageExecution

        runs = visible_runs(
            WorkflowRun.objects.filter(organization_id=caller), caller, Permission.WORKFLOW_TRIGGER
        )
        # A gate naming specific approver addresses the caller isn't one of
        # is filtered out below, after the DB slice. Declared approvers are
        # almost always team/role slugs rather than addresses in practice,
        # and this mirrors the exact check the decide path applies
        # (``may_decide_human_gate``), so the two never disagree.
        rows = (
            WorkflowStageExecution.objects.filter(
                stage__kind=WorkflowStage.StageKind.HUMAN_GATE,
                workflow_run_id__in=runs.values("pk"),
            )
            .exclude(status__in=WorkflowStageExecution.TERMINAL_STATUSES)
            .select_related("stage__definition", "workflow_run")
            .order_by("-started_at", "-pk")[: max(1, min(int(limit), 200))]
        )
        user = info.context.user
        return [
            pending_gate_to_type(execution)
            for execution in rows
            if may_decide_human_gate(user, execution.stage.approvers)
        ]
