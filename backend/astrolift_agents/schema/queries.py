"""Read-only queries for the Agent Dispatch Layer.

Skills, ToolDefs, Briefs, and AgentTasks are org-scoped; the
``dispatchers`` resolver is platform-level (the routing fabric spans
tenants) and is staff/superuser-only.

Every resolver carries ``@require_permission`` + ``@tenant_scoped`` per
the tenancy guardrail. ``@tenant_scoped`` only asserts a tenant context
exists — it does not filter — so each resolver applies its own org
``Q`` and rejects an ``org_id`` argument that doesn't match the caller's
active tenant (a non-superuser may not read another org's rows).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

import strawberry
from _sdk.k8s_naming import agent_namespace
from graphql import GraphQLError
from strawberry.types import Info

from astrolift_agents.models import (
    AgentBox,
    AgentEnvironmentSpec,
    AgentInteraction,
    AgentSecretBundleRef,
    AgentTask,
    Brief,
    DispatcherInstance,
    OrgSkillRepo,
    Skill,
    ToolDef,
)
from astrolift_agents.schema.types import (
    AgentBoxType,
    AgentDetailType,
    AgentEnvironmentSpecType,
    AgentInteractionType,
    AgentListItemType,
    AgentLiveStatusType,
    AgentRuntimeType,
    AgentSecretBundleAttachmentType,
    AgentSecretBundleType,
    AgentSecretStatusType,
    AgentTaskType,
    AgentTriggerType,
    BriefType,
    DiscoveredAgentManifestType,
    DispatcherInstanceType,
    OrgSkillRepoType,
    ScanAgentManifestsResultType,
    SkillType,
    ToolDefType,
    agent_box_to_type,
    agent_detail_to_type,
    agent_env_spec_to_type,
    agent_interaction_to_type,
    agent_secret_bundle_attachment_to_type,
    agent_secret_bundle_to_type,
    agent_secret_status_to_type,
    agent_task_to_type,
    agent_trigger_to_type,
    brief_to_type,
    dispatcher_to_type,
    org_skill_repo_to_type,
    skill_to_type,
    tool_def_to_type,
)
from astrolift_graphql import GUID, PageType, keyset_page, search_q
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

log = logging.getLogger(__name__)


def _valid_guid(value) -> str | None:
    """Return ``value`` as a canonical UUID string, or None if it isn't one.

    Guards by-id resolvers reachable from user-controlled route params (e.g.
    ``/agents/runs/<task>``): a non-UUID like ``"overview"`` would otherwise
    reach a ``UUIDField`` filter and raise a ValidationError -> HTTP 500
    ("'overview' is not a valid UUID") instead of resolving to a clean
    not-found. Callers treat None as "no such row".
    """
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def _caller_org_id(info: Info, org_id: strawberry.ID) -> int:
    """Resolve the caller's active organization, asserting it matches
    the requested ``org_id`` GUID.

    ``@tenant_scoped`` guarantees a tenant context exists; this enforces
    that the explicit ``org_id`` argument refers to that same org so a
    caller can't read another tenant's rows by passing a foreign GUID.
    Superusers bypass the match (their tenant context may differ).
    """
    tenant = get_current_tenant()
    org = tenant.organization_id if tenant else None
    if org is None:
        raise GraphQLError("no active organization")

    from astrolift_identity.models import Organization

    requested = Organization.objects.filter(guid=str(org_id), deleted_at__isnull=True).first()
    if requested is None:
        raise GraphQLError("organization not found")

    user = getattr(getattr(info.context, "request", None), "user", None)
    is_super = bool(getattr(user, "is_superuser", False))
    if requested.pk != org and not is_super:
        raise GraphQLError("organization mismatch")
    return requested.pk


# ---------------------------------------------------------------------------
# Agent list + live-status helpers (spec 33 PR-2)
# ---------------------------------------------------------------------------
#
# The Agents list and the per-agent detail header read ``kind: agent``
# Workload rows plus a rollup of their AgentRun execution state. These
# helpers keep the rollup to a bounded number of queries (no per-agent
# N+1) and compute the next scheduled firing off the platform's existing
# cron evaluator — no new dependency.

# Cap the agent list / live-status rollup so a tenant with a large fleet
# can't issue an unbounded scan. Mirrors the 200-row soft cap the sibling
# agent resolvers (agent_tasks, skills, ...) already use.
_AGENT_LIST_CAP = 200

# Upper bound on the fleet-map transition feed (#1091). The client passes
# ``limit=200`` and drains forward across polls via the ``since`` cursor, so
# this only bounds a caller that requests a larger page — a busy fleet still
# converges to live over a few polls rather than issuing one unbounded scan.
_AGENT_TRANSITIONS_CAP = 500

# Upper bound on the per-task interaction feed (#1216). Like the transitions
# feed, the client passes ``limit=200`` and drains forward across polls via
# the ``since`` cursor, so this only bounds a caller that requests more.
_AGENT_INTERACTIONS_CAP = 500

# How far ahead ``_next_cron_fire`` scans for the next firing. A valid
# 5-field cron with a day-of-month + month constraint can be up to ~13
# months out (e.g. ``0 0 29 2 *`` on a non-leap year), so a 400-day window
# covers every expression the registry's validator accepts. The scan is
# minute-granular and pure-Python; the cap bounds the worst case rather
# than being expected to bite (most schedules fire within a day).
_NEXT_CRON_LOOKAHEAD_MINUTES = 400 * 24 * 60


def _next_cron_fire(expression: str, *, after):
    """Next UTC firing of ``expression`` strictly after ``after``.

    Reuses the platform's existing cron evaluator
    (:func:`astrolift_workflows.cron_deploy.cron_matches`) — the same
    5-field grammar the registry validates at the mutation boundary — by
    scanning forward minute-by-minute. Returns a tz-aware ``datetime``
    truncated to the minute, or ``None`` when the expression is empty /
    invalid or no firing falls within :data:`_NEXT_CRON_LOOKAHEAD_MINUTES`.

    Scanning (rather than pulling in ``croniter``) keeps the dependency
    surface flat per the build plan; the look-ahead cap bounds the cost.
    """
    from datetime import timedelta

    from astrolift_workflows.cron_deploy import cron_matches

    if not (expression or "").strip():
        return None
    # Start at the top of the minute *after* ``after`` so a cron that
    # matches the current minute reports the *next* occurrence, not now.
    cursor = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(_NEXT_CRON_LOOKAHEAD_MINUTES):
        try:
            if cron_matches(expression, now=cursor):
                return cursor
        except ValueError:
            # cron_matches rejects naive datetimes; ``after`` is always
            # tz-aware here (timezone.now()), so this can't fire — but
            # refuse to loop forever on a bad expression just in case.
            return None
        cursor += timedelta(minutes=1)
    return None


def _service_replica_status(workload) -> tuple[int | None, int | None, bool | None]:
    """Live ``(desired, ready, deployment_ready)`` for a Service-family agent.

    The Deployment IS the run (#1012). Best-effort: any resolution/read error
    (no cluster bound, driver missing, transient) degrades to ``(None, None,
    None)`` so the agent list never fails on one unreachable Deployment.
    ``deployment_ready`` is True when ready==desired and desired>0, False when
    deployed but not yet converged, None when not deployed.
    """
    from astrolift_lifecycle.services.k8s_ops import read_workload_status

    try:
        status = read_workload_status(workload)
    except Exception:  # noqa: BLE001 — observe surface must not raise
        return None, None, None
    if not status.deployed:
        return None, None, None
    desired = status.desired_replicas
    ready = status.ready_replicas
    ready_flag = ready == desired and (desired or 0) > 0
    return desired, ready, ready_flag


def _agent_run_rollup(workload_pks: list[int]) -> dict[int, dict]:
    """Bulk last-run + running-count per agent workload.

    Returns ``{workload_pk: {"last_status", "last_at", "running"}}`` for
    every pk in ``workload_pks`` (missing keys mean "no runs yet"). Two
    queries total regardless of fleet size — one ordered scan that keeps
    the first (newest) row per workload off the ``(workload, -created_at)``
    index, and one grouped count of RUNNING rows — so the list resolvers
    never spawn a per-agent query.
    """
    from django.db.models import Count

    from astrolift_lifecycle.models import AgentRun

    if not workload_pks:
        return {}

    rollup: dict[int, dict] = {}

    # Newest run per workload. The index is (workload, -created_at), so the
    # first row seen per workload_id in this ordering is its latest run.
    latest_seen: set[int] = set()
    for run in (
        AgentRun.objects.filter(workload_id__in=workload_pks)
        .order_by("workload_id", "-created_at")
        .values_list("workload_id", "status", "created_at", "started_at")
    ):
        wl_id, status, created_at, started_at = run
        if wl_id in latest_seen:
            continue
        latest_seen.add(wl_id)
        rollup.setdefault(wl_id, {})
        rollup[wl_id]["last_status"] = status
        # Prefer the actual start; fall back to created for pending rows
        # that never started so the "last ran" chip still has a timestamp.
        rollup[wl_id]["last_at"] = started_at or created_at

    # Running count per workload — one grouped aggregate.
    for row in (
        AgentRun.objects.filter(
            workload_id__in=workload_pks,
            status=AgentRun.Status.RUNNING,
        )
        .values("workload_id")
        .annotate(n=Count("pk"))
    ):
        rollup.setdefault(row["workload_id"], {})
        rollup[row["workload_id"]]["running"] = int(row["n"])

    return rollup


def _agent_workload_qs(org_pk: int, *, project_slug: str | None = None):
    """Base queryset of the caller-org's ``kind: agent`` workloads.

    Scoped to ``org_pk`` via the workload's app organization (Workload
    has no org FK of its own). ``project_slug`` further narrows to one
    project. Soft-deleted workloads (and rows under a soft-deleted app)
    are excluded; ordered newest-first for a stable list.
    """
    from astrolift_registry.models import Workload

    qs = Workload.objects.filter(
        kind=Workload.Kind.AGENT,
        registered_app__organization_id=org_pk,
        registered_app__deleted_at__isnull=True,
        deleted_at__isnull=True,
    ).select_related("registered_app", "registered_app__project")
    if project_slug:
        qs = qs.filter(registered_app__project__slug=project_slug)
    return qs.order_by("-created_at")


def _agent_triggers_qs(org_pk: int, *, agent_slug: str, search: str | None = None):
    """Filtered, unordered ``WorkflowWebhook`` stream bound to one agent.

    Shared by ``agent_triggers`` and its paginated sibling so the two can
    never disagree about what a trigger row is. Ordering is deliberately
    not applied here — ``keyset_page`` imposes it from the seek key.

    Module-level for the same reason ``_agent_list_rows`` is: Strawberry
    binds ``self`` on a root field resolver to the schema's root value,
    which the Django view leaves as ``None``.

    The agent is resolved through ``_agent_workload_qs(org_pk)``, so a
    slug belonging to another org (or to no agent at all) yields a
    queryset that matches nothing rather than another tenant's bindings.
    """
    from astrolift_agents.models.workflow_trigger import WorkflowWebhook

    workload = _agent_workload_qs(org_pk).filter(slug=agent_slug).first()
    if workload is None:
        return WorkflowWebhook.objects.none()
    qs = WorkflowWebhook.objects.filter(
        agent_definition=workload,
        organization_id=org_pk,
    ).select_related("organization")
    if search:
        qs = qs.filter(search_q(search, "slug", "scm_repo", "branch_pattern"))
    return qs


def _agent_list_rows(info: Info, org_id: strawberry.ID, project_slug: str | None) -> list[AgentListItemType]:
    """Build the agent list rows for an org (+ optional project filter).

    Module-level so both ``agent_workloads`` and ``agent_fleet`` can call it.
    The fleet resolver previously did ``self.agent_workloads(...)``, but
    Strawberry passes the root value (``None``) as ``self`` on a field
    resolver, so that raised ``'NoneType' has no attribute 'agent_workloads'``.
    """
    org_pk = _caller_org_id(info, org_id)
    workloads = list(_agent_workload_qs(org_pk, project_slug=project_slug)[:_AGENT_LIST_CAP])
    rollup = _agent_run_rollup([w.pk for w in workloads])
    rows: list[AgentListItemType] = []
    for w in workloads:
        stats = rollup.get(w.pk, {})
        app = w.registered_app
        rows.append(
            AgentListItemType(
                id=GUID(str(w.guid)),
                name=w.name,
                slug=w.slug,
                app_slug=app.slug,
                project_slug=(app.project.slug if app.project_id else ""),
                source_repo=app.source_repo or "",
                source_url=app.source_url or "",
                run_family=w.run_family,
                run_mode=w.run_mode,
                run_paused=w.run_paused,
                run_cron_expression=w.run_cron_expression or "",
                last_run_status=stats.get("last_status"),
                last_run_at=stats.get("last_at"),
                running_count=stats.get("running", 0),
                run_max_parallel=w.run_max_parallel,
                replicas=w.replicas,
                scheduled_scale_to=w.scheduled_scale_to,
                scale_up_cron=w.scale_up_cron or "",
                scale_down_cron=w.scale_down_cron or "",
            )
        )
    return rows


@strawberry.type
class AgentsQuery:
    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def skills(self, info: Info, org_id: strawberry.ID, is_global: bool = False) -> list[SkillType]:
        """Org skills plus all global skills.

        ``is_global=True`` narrows to global skills only; otherwise the
        org's own skills are unioned with the global catalog (both
        readable by every org).
        """
        from django.db.models import Q

        org_pk = _caller_org_id(info, org_id)
        if is_global:
            scope = Q(is_global=True)
        else:
            scope = Q(organization_id=org_pk) | Q(is_global=True)
        qs = Skill.objects.filter(scope, deleted_at__isnull=True).order_by("-is_global", "slug")[:200]
        return [skill_to_type(s) for s in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def skill(self, info: Info, id: strawberry.ID) -> SkillType | None:
        """One skill by GUID, scoped to the caller's org or the global
        catalog. Foreign-org skills resolve to null (not an error) so
        the surface doesn't leak existence across tenants."""
        from django.db.models import Q

        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        row = Skill.objects.filter(
            Q(organization_id=org_pk) | Q(is_global=True),
            guid=str(id),
            deleted_at__isnull=True,
        ).first()
        return skill_to_type(row) if row is not None else None

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def tool_defs(self, info: Info, skill_id: strawberry.ID) -> list[ToolDefType]:
        """ToolDefs attached to ``skill_id``. The parent skill must be
        readable by the caller's org (or global), else an empty list."""
        from django.db.models import Q

        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        skill = Skill.objects.filter(
            Q(organization_id=org_pk) | Q(is_global=True),
            guid=str(skill_id),
            deleted_at__isnull=True,
        ).first()
        if skill is None:
            return []
        qs = ToolDef.objects.filter(skill=skill, deleted_at__isnull=True).order_by("slug")[:200]
        return [tool_def_to_type(t) for t in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def org_tool_defs(self, info: Info, org_id: strawberry.ID) -> list[ToolDefType]:
        """All ToolDefs across every skill visible to ``org_id`` (own +
        global). Allows the Tool Registry UI to list all tools without
        fetching per-skill. Capped at 500 rows."""
        from django.db.models import Q

        org_pk = _caller_org_id(info, org_id)
        skill_scope = Q(skill__organization_id=org_pk) | Q(skill__is_global=True)
        qs = (
            ToolDef.objects.filter(skill_scope, deleted_at__isnull=True)
            .select_related("skill")
            .order_by("skill__slug", "slug")[:500]
        )
        return [tool_def_to_type(t) for t in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def brief(self, info: Info, id: strawberry.ID) -> BriefType | None:
        """One Brief by GUID, scoped to the caller's org."""
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        row = Brief.objects.filter(guid=str(id), organization_id=org_pk, deleted_at__isnull=True).first()
        return brief_to_type(row) if row is not None else None

    @strawberry.field
    @require_permission(Permission.SCM_READ)
    @tenant_scoped()
    def org_skill_repos(self, info: Info, org_id: strawberry.ID) -> list[OrgSkillRepoType]:
        """The org's registered skill repos (spec 39d), ordered by alias.

        Org-scoped: ``org_id`` must match the caller's active tenant
        (superusers excepted, via ``_caller_org_id``); the queryset is filtered
        to that org. Gated on ``scm.read`` — the same read grant the source
        connections list uses, since a skill repo is a source reference. The
        linked credential is never surfaced (only ``sourceConnectionId`` + the
        ``isPrivate`` derivation).
        """
        org_pk = _caller_org_id(info, org_id)
        qs = (
            OrgSkillRepo.objects.filter(organization_id=org_pk, deleted_at__isnull=True)
            .select_related("source_connection")
            .order_by("alias")[:200]
        )
        return [org_skill_repo_to_type(r) for r in qs]

    @strawberry.field
    @require_permission(Permission.AGENT_READ)
    @tenant_scoped()
    def agent_tasks(
        self,
        info: Info,
        org_id: strawberry.ID,
        status: str | None = None,
        workload_id: strawberry.ID | None = None,
    ) -> list[AgentTaskType]:
        """The org's AgentTasks, newest first, optionally filtered by
        status and/or agent workload.

        ``workload_id`` (spec 33 PR-2) narrows to tasks dispatched from
        one ``kind: agent`` Workload — the per-agent detail surface uses
        it to show a single agent's task history. The GUID is resolved
        within the caller's org first; a workload GUID that belongs to
        another org (or doesn't exist) yields an empty list rather than
        leaking another tenant's tasks. An unknown status string also
        yields an empty list rather than an error.
        """
        org_pk = _caller_org_id(info, org_id)
        qs = AgentTask.objects.filter(organization_id=org_pk, deleted_at__isnull=True)
        if status:
            qs = qs.filter(status=status)
        if workload_id:
            # Resolve the workload inside the caller's org (its app's
            # organization must match) so a foreign-org GUID can't be
            # used to filter — and so a no-match returns [] instead of
            # the org's entire task list (an unfiltered filter).
            from astrolift_registry.models import Workload

            wl = (
                Workload.objects.filter(
                    guid=str(workload_id),
                    registered_app__organization_id=org_pk,
                    deleted_at__isnull=True,
                )
                .values_list("pk", flat=True)
                .first()
            )
            if wl is None:
                return []
            qs = qs.filter(agent_definition_id=wl)
        # select_related the org (snapshot_url presigning) plus the dispatcher
        # + its cluster, which the node-layer projection (#1091) reads — so the
        # list stays a bounded number of queries with no per-task N+1.
        qs = qs.select_related(
            "organization",
            "project",
            "agent_definition",
            "dispatcher",
            "dispatcher__tenant_cluster",
        ).order_by("-created_at")[:200]
        return [agent_task_to_type(t) for t in qs]

    @strawberry.field
    @require_permission(Permission.AGENT_TASK_WATCH)
    @tenant_scoped()
    def agent_gallery(self, info: Info, org_id: strawberry.ID) -> list[AgentTaskType]:
        """The org's *watchable* agent tasks — the VNC theatre roster.

        Narrows to RUNNING, VNC-capable tasks that have a published relay
        path (``vnc_url``), newest first. Each row carries ``vnc_url`` (the
        live RFB relay the theatre connects to) and ``snapshot_url`` (a
        short-lived presigned GET for the latest framebuffer JPEG the gallery
        tiles poll). Gated on ``agent_task.watch`` — the same operator-grade
        permission the live VNC relay enforces — rather than plain
        ``app.read``, so the gallery never lists a session the caller could
        not actually open.
        """
        org_pk = _caller_org_id(info, org_id)
        qs = (
            AgentTask.objects.filter(
                organization_id=org_pk,
                status=AgentTask.Status.RUNNING,
                vnc_enabled=True,
                deleted_at__isnull=True,
            )
            .exclude(vnc_url="")
            .select_related(
                "organization",
                "project",
                "agent_definition",
                "dispatcher",
                "dispatcher__tenant_cluster",
            )
            .order_by("-started_at", "-created_at")[:200]
        )
        return [agent_task_to_type(t) for t in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def agent_task(self, info: Info, id: strawberry.ID) -> AgentTaskType | None:
        """One AgentTask by GUID, scoped to the caller's org."""
        guid = _valid_guid(id)
        if guid is None:
            return None
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        row = (
            AgentTask.objects.filter(guid=guid, organization_id=org_pk, deleted_at__isnull=True)
            .select_related(
                "organization",
                "project",
                "agent_definition",
                "dispatcher",
                "dispatcher__tenant_cluster",
            )
            .first()
        )
        return agent_task_to_type(row) if row is not None else None

    @strawberry.field
    @require_permission(Permission.AGENT_READ)
    @tenant_scoped()
    def agent_task_transitions_since(
        self,
        info: Info,
        org_id: strawberry.ID,
        since: datetime | None = None,
        limit: int = 200,
    ) -> list[AgentTaskType]:
        """The caller-org's AgentTasks whose state changed after ``since``,
        oldest change first — the live feed behind /fleet/map (#1091).

        The fleet map polls this with a moving ``since`` cursor (the
        high-water ``updated_at`` of the previous batch) and merges each
        batch into the map, so an edge pulses whenever a task advances
        state. ``AgentTask.transition_to`` bumps ``updated_at`` on every
        transition, so ``updated_at`` is the transition cursor — there is no
        separate transition-log model. With ``since`` omitted the feed
        returns the oldest ``limit`` tasks in the org; the client seeds the
        cursor with a recent lookback so the first frame shows the live
        fleet rather than ancient history.

        Org-scoped exactly like :meth:`agent_tasks`: ``org_id`` must match
        the caller's active tenant (superusers excepted, via
        ``_caller_org_id``) and the queryset is filtered to that org, so a
        cross-org call never sees another tenant's tasks. Ordered ascending
        by ``updated_at`` and capped (:data:`_AGENT_TRANSITIONS_CAP`) so a
        busy fleet drains forward across polls instead of returning an
        unbounded scan. ``dispatcher`` (and its ``tenant_cluster``) is
        selected so the map's dispatcher/cluster layers project without an
        N+1.
        """
        org_pk = _caller_org_id(info, org_id)
        capped = max(1, min(limit, _AGENT_TRANSITIONS_CAP))
        qs = AgentTask.objects.filter(organization_id=org_pk, deleted_at__isnull=True)
        if since is not None:
            qs = qs.filter(updated_at__gt=since)
        qs = qs.select_related(
            "organization",
            "project",
            "agent_definition",
            "dispatcher",
            "dispatcher__tenant_cluster",
        ).order_by("updated_at")[:capped]
        return [agent_task_to_type(t) for t in qs]

    @strawberry.field
    @require_permission(Permission.AGENT_READ)
    @tenant_scoped()
    def agent_task_interactions(
        self,
        info: Info,
        org_id: strawberry.ID,
        task_id: strawberry.ID,
        since: datetime | None = None,
        limit: int = 200,
    ) -> list[AgentInteractionType]:
        """Control-plane-observed interactions for one AgentTask (#1216).

        The per-agent interaction feed behind the LiveFlowMap P3 map: every
        captured Control API call / signal / gate for ``task_id``, oldest
        change first. Polled with a moving ``since`` cursor (the high-water
        ``occurred_at`` of the previous batch) exactly like
        :meth:`agent_task_transitions_since`, and capped so a busy task
        drains forward across polls rather than returning an unbounded scan.

        Org-scoped and FAIL-CLOSED: ``org_id`` must match the caller's
        active tenant (superusers excepted, via ``_caller_org_id``); the
        interaction queryset is filtered to that org *explicitly*
        (``organization_id=org_pk``) — never relying on the decorator alone,
        which only asserts a tenant context exists. The task is resolved
        within the caller's org first, so a foreign-org / unknown / non-UUID
        ``task_id`` yields ``[]`` (never another tenant's rows, never a 500).
        """
        org_pk = _caller_org_id(info, org_id)
        guid = _valid_guid(task_id)
        if guid is None:
            return []
        # Resolve the task inside the caller's org so a foreign-org task id
        # reads as empty rather than leaking task existence across tenants.
        task_pk = (
            AgentTask.objects.filter(
                guid=guid,
                organization_id=org_pk,
                deleted_at__isnull=True,
            )
            .values_list("pk", flat=True)
            .first()
        )
        if task_pk is None:
            return []
        capped = max(1, min(limit, _AGENT_INTERACTIONS_CAP))
        qs = AgentInteraction.objects.filter(organization_id=org_pk, agent_task_id=task_pk)
        if since is not None:
            qs = qs.filter(occurred_at__gt=since)
        qs = qs.order_by("occurred_at")[:capped]
        return [agent_interaction_to_type(r) for r in qs]

    @strawberry.field
    @require_permission(Permission.AGENT_READ)
    @tenant_scoped()
    def agent_task_logs(self, info: Info, id: strawberry.ID, tail: int = 200) -> list[str]:
        """Recent stdout/stderr lines from an AgentTask's pod.

        Resolves the AgentTask by GUID, tenant-scoped exactly like
        :meth:`agent_task` (the task must belong to the caller's active
        org; a foreign-org id resolves to ``[]``, not an error, so the
        surface doesn't leak task existence across tenants). Reads the
        pod's logs through the same driver plumbing the app-log surface
        uses — :func:`core.cluster_observability.fetch_task_pod_logs`,
        which discovers the pod via ``list_app_pods`` and reads it with
        ``stream_app_logs(follow=False)`` — and returns up to ``tail``
        of the most recent message lines.

        Gated on ``app.read_logs`` (the log-specific permission, same as
        the live-tail subscription) rather than plain ``app.read``.

        Returns ``[]`` — never a 500 — for every empty case: the task
        doesn't exist for the tenant, the task's dispatcher has no
        ``tenant_cluster`` bound, the cluster can't be turned into a
        usable driver, or the pod has produced no logs yet.

        Those cases are indistinguishable to the caller, which is what
        makes an empty result so expensive to investigate (#1712): the
        operator sees no output, no error and exit 0, and the one moment
        they need this most is a failed agent. Each of them now logs why
        at INFO with the task guid, so the control-plane log says which
        path was taken even though the GraphQL shape cannot.

        Note on pod discovery: the K8s Job spawner labels each agent pod
        ``astrolift.dev/task-id=<task.guid>`` and the namespace the Job
        actually landed in is frozen on ``AgentTask.namespace`` at spawn
        (#891). Discovery passes the task guid as a ``task_id`` selector,
        which the live pod backend turns into an
        ``astrolift.dev/task-id=<guid>`` label query, so an agent pod is
        found exactly on a real cluster; the recorded Job name on
        ``AgentTask.pod_name`` remains the last-resort fallback.
        """
        from asgiref.sync import async_to_sync

        from core.cluster_observability import fetch_task_pod_logs

        def _empty(reason: str) -> list[str]:
            log.info("agent_task_logs id=%s -> no lines: %s", id, reason)
            return []

        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        if org_pk is None:
            return _empty("no active organization in the request")

        guid = _valid_guid(id)
        if guid is None:
            return _empty("id is not a valid task guid")

        # Resolve the task + its dispatcher's cluster off the event loop
        # (Django ORM is sync). Returns the data the async log fetch
        # needs, or None when there is no readable pod to target.
        def _resolve() -> tuple[Any, str, str, str] | None:
            row = (
                AgentTask.objects.filter(guid=guid, organization_id=org_pk, deleted_at__isnull=True)
                .select_related("organization", "dispatcher", "dispatcher__tenant_cluster")
                .first()
            )
            if row is None:
                return "no task with this guid in the caller's organization"
            # Resolve the same cluster the spawner placed the agent Job on.
            # Nothing sets AgentTask.dispatcher at dispatch, so prefer the
            # dispatcher's cluster when present (legacy/explicit), else fall
            # back to the org's managed cluster via the identical helper the
            # spawner uses (_resolve_managed_cluster). The one-shot read no
            # longer hangs — the follow=False stream now terminates on EOF and
            # fetch_task_pod_logs is hard-bounded by a read timeout (#1013).
            dispatcher = row.dispatcher
            cluster = dispatcher.tenant_cluster if dispatcher is not None else None
            if cluster is None:
                from astrolift_workflows.activities.agent_stage import (
                    _resolve_managed_cluster,
                )

                try:
                    cluster = _resolve_managed_cluster(row.organization)
                except Exception as exc:
                    return f"could not resolve a managed cluster for the org: {exc}"
            if cluster is None:
                return "the org has no managed cluster to read the pod from"
            if not getattr(cluster, "is_active", True):
                return f"cluster {getattr(cluster, 'slug', '?')} is not active"
            # Read the namespace the dispatcher actually spawned into off the
            # task (#891) — different dispatch paths land in different
            # namespaces, so recomputing it can miss the pod. Fall back to the
            # per-org agent namespace for pre-#891 rows that never stamped it.
            namespace = (row.namespace or "").strip()
            if not namespace:
                org_slug = (getattr(row.organization, "slug", "") or "").strip()
                if not org_slug:
                    return "the task's organization has no slug to derive a namespace from"
                namespace = agent_namespace(org_slug)
            return cluster, namespace, str(row.guid), (row.pod_name or "")

        resolved = _resolve()
        if isinstance(resolved, str):
            return _empty(resolved)
        cluster, namespace, task_guid, pod_name_hint = resolved

        # The /app/gql GraphQL view runs sync (threadpool, no event loop), so
        # bridge the async pod-log fetch with async_to_sync rather than making
        # the resolver async (which the sync view can't drive).
        lines = async_to_sync(fetch_task_pod_logs)(
            cluster=cluster,
            namespace=namespace,
            task_guid=task_guid,
            pod_name_hint=pod_name_hint,
            tail=tail,
        )
        if not lines:
            # The pod is the usual answer here: an agent Job carries
            # ``ttlSecondsAfterFinished``, so its pod is garbage-collected
            # an hour after it settles and there is nothing left to read.
            _empty(
                f"no pod logs found on cluster={getattr(cluster, 'slug', '?')} "
                f"namespace={namespace} pod_hint={pod_name_hint or '(none)'} — "
                f"the pod may have been garbage-collected after the Job's TTL"
            )
        return lines

    @strawberry.field
    @require_permission(Permission.AGENT_ENV_SPEC_READ)
    @tenant_scoped()
    def agent_environment_specs(self, info: Info, org_id: strawberry.ID) -> list[AgentEnvironmentSpecType]:
        """The org's AgentEnvironmentSpecs, ordered by slug.

        Org-scoped: ``org_id`` must match the caller's active tenant
        (superusers excepted) — the spec carries secret *references* the
        dispatcher resolves at launch, so it must never leak across orgs.
        """
        org_pk = _caller_org_id(info, org_id)
        qs = AgentEnvironmentSpec.objects.filter(organization_id=org_pk, deleted_at__isnull=True).order_by(
            "slug"
        )[:200]
        return [agent_env_spec_to_type(s) for s in qs]

    @strawberry.field
    @require_permission(Permission.AGENT_READ)
    @tenant_scoped()
    def agent_boxes(
        self, info: Info, org_id: strawberry.ID, include_ended: bool = False
    ) -> list[AgentBoxType]:
        """The org's agent-boxes, warm ones first (#128).

        The default question an operator or an IDE is asking is "what can I
        attach to", so a settled box is left out unless asked for: it is
        history, and listing it beside live boxes invites attaching to
        something that no longer exists. ``include_ended`` brings the
        reaped/stopped/failed rows back for the "why did my box go away"
        case.
        """
        org_pk = _caller_org_id(info, org_id)
        qs = AgentBox.objects.filter(organization_id=org_pk, deleted_at__isnull=True)
        if not include_ended:
            qs = qs.filter(status__in=sorted(AgentBox.LIVE_STATUSES))
        qs = qs.select_related("organization", "agent_definition", "environment_spec").order_by(
            "-created_at"
        )[:200]
        return [agent_box_to_type(b) for b in qs]

    @strawberry.field
    @require_permission(Permission.AGENT_READ)
    @tenant_scoped()
    def agent_box(self, info: Info, slug: str) -> AgentBoxType | None:
        """One box by slug, scoped to the caller's org.

        The slug is what a client stores between sessions, so this is the
        lookup an IDE polls while a box provisions. Another org's box
        resolves to null rather than an error, so the surface leaks no
        existence.
        """
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        row = (
            AgentBox.objects.filter(slug=slug, organization_id=org_pk, deleted_at__isnull=True)
            .select_related("organization", "agent_definition", "environment_spec")
            .first()
        )
        return agent_box_to_type(row) if row is not None else None

    @strawberry.field
    @require_permission(Permission.AGENT_ENV_SPEC_READ)
    @tenant_scoped()
    def agent_environment_spec(self, info: Info, slug: str) -> AgentEnvironmentSpecType | None:
        """One AgentEnvironmentSpec by slug, scoped to the caller's org.

        A spec in another org resolves to null (not an error) so the
        surface doesn't leak existence across tenants.
        """
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        row = AgentEnvironmentSpec.objects.filter(
            slug=slug, organization_id=org_pk, deleted_at__isnull=True
        ).first()
        return agent_env_spec_to_type(row) if row is not None else None

    @strawberry.field
    @require_permission(Permission.SECRET_LIST)
    @tenant_scoped()
    def agent_environment_spec_secret_status(self, info: Info, slug: str) -> list[AgentSecretStatusType]:
        """Per-ref presence status for a spec's ``secret_refs`` — metadata
        only (env var, uri, exists), never values.

        Scoped to the caller's org. A ref whose store read fails reports
        ``exists=false`` with a short ``error`` string rather than failing
        the whole query (mirrors the bundle-key swallow-and-report). A spec
        in another org (or absent) resolves to ``[]``.
        """
        from astrolift_agents.services.agent_cluster import (
            NoAgentClusterError,
            resolve_agent_cluster,
        )
        from astrolift_dispatch.agent_secrets import effective_secret_refs, probe_ref_statuses

        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        spec = (
            AgentEnvironmentSpec.objects.select_related("organization")
            .filter(slug=slug, organization_id=org_pk, deleted_at__isnull=True)
            .first()
        )
        if spec is None:
            return []
        try:
            cluster = resolve_agent_cluster(spec.organization)
        except NoAgentClusterError:
            cluster = None
        rows = probe_ref_statuses(cluster=cluster, refs=effective_secret_refs(spec))
        return [agent_secret_status_to_type(r) for r in rows]

    @strawberry.field
    @require_permission(Permission.SECRET_LIST)
    @tenant_scoped()
    def agent_secret_bundles(self, info: Info, env_spec_slug: str) -> list[AgentSecretBundleType]:
        """Reusable bundles visible to this agent's organization."""
        from astrolift_agents.services.agent_cluster import resolve_agent_cluster
        from astrolift_agents.services.project_membership import agent_spec_belongs_to_project
        from astrolift_dispatch.agent_secrets import (
            resolve_secrets_backend,
            secret_backend_capabilities,
        )
        from astrolift_services.models import SecretBundle

        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        spec = AgentEnvironmentSpec.objects.filter(
            slug=env_spec_slug, organization_id=org_pk, deleted_at__isnull=True
        ).first()
        if spec is None:
            return []
        backend = None
        try:
            backend = resolve_secrets_backend(resolve_agent_cluster(spec.organization))
        except Exception:  # noqa: BLE001 - capability metadata degrades in UI
            backend = None
        capabilities = secret_backend_capabilities(backend)
        bundles = (
            SecretBundle.objects.select_related("project")
            .filter(
                organization_id=org_pk,
                team__isnull=True,
                deleted_at__isnull=True,
            )
            .order_by("name", "slug")[:200]
        )
        visible = [
            bundle
            for bundle in bundles
            if bundle.project_id is None or agent_spec_belongs_to_project(spec, bundle.project)
        ]
        return [agent_secret_bundle_to_type(bundle, capabilities) for bundle in visible]

    @strawberry.field
    @require_permission(Permission.SECRET_READ, Permission.SECRET_LIST)
    @tenant_scoped()
    def agent_environment_spec_secret_bundle_attachments(
        self, info: Info, slug: str
    ) -> list[AgentSecretBundleAttachmentType]:
        """Ordered reusable-secret bundles attached to one agent spec."""
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        refs = (
            AgentSecretBundleRef.objects.select_related("environment_spec", "secret_bundle")
            .filter(
                environment_spec__slug=slug,
                environment_spec__organization_id=org_pk,
                environment_spec__deleted_at__isnull=True,
                secret_bundle__deleted_at__isnull=True,
                deleted_at__isnull=True,
            )
            .order_by("position", "created_at", "pk")
        )
        return [agent_secret_bundle_attachment_to_type(ref) for ref in refs]

    # ----------------------------------------------------------------
    # Agent list + live status (spec 33 PR-2)
    # ----------------------------------------------------------------

    @strawberry.field
    @require_permission(Permission.AGENT_READ)
    @tenant_scoped()
    def agent_workloads(
        self, info: Info, org_id: strawberry.ID, project_slug: str | None = None
    ) -> list[AgentListItemType]:
        """The org's ``kind: agent`` workloads as list rows, newest first.

        ``project_slug`` narrows to one project's agents (the
        project-scoped Agents list); omitting it returns every agent in
        the org's fleet (the fleet/org-wide variant). Each row carries
        the run-spec fields (PR-1) plus a compact last-run summary +
        running count rolled up in bulk so the list stays a single
        round-trip with no per-agent query.

        Org-scoped: ``org_id`` must match the caller's active tenant
        (superusers excepted, via ``_caller_org_id``); the workload
        queryset is filtered to that org through the app's organization.
        """
        return _agent_list_rows(info, org_id, project_slug)

    @strawberry.field
    @require_permission(Permission.AGENT_READ)
    @tenant_scoped()
    def agent_fleet(self, info: Info, org_id: strawberry.ID) -> list[AgentListItemType]:
        """Org-wide agent fleet — every ``kind: agent`` workload across
        all projects in the caller's org.

        Convenience alias for :meth:`agent_workloads` with no project
        filter, exposed as its own field so the fleet/global Agents page
        doesn't have to special-case a null ``project_slug`` argument.
        """
        return _agent_list_rows(info, org_id, project_slug=None)

    @strawberry.field
    @require_permission(Permission.AGENT_READ)
    @tenant_scoped()
    def agent(self, info: Info, org_id: strawberry.ID, slug: str) -> AgentDetailType | None:
        """One agent's full read bundle by slug (spec 38 Phase 4).

        Backs the per-agent Build tab: resolves the ``kind: agent``
        :class:`Workload` whose slug is ``slug`` within the caller's org
        and returns its identity + run-spec basics, primary-container
        image, definitional Brief (nullable), and attached Skills (ordered
        by :class:`AgentSkillRef` position) with each skill's ToolDefs
        nested.

        Org-scoped exactly like :meth:`agent_workloads`: ``org_id`` must
        match the caller's active tenant (superusers excepted, via
        ``_caller_org_id``), and the workload is filtered to that org
        through the app's organization. A slug that belongs to another
        org — or doesn't exist — resolves to ``null`` (not an error and
        not another tenant's agent) so the surface doesn't leak existence
        across tenants. The slug is unique only *within* an org, so the
        org filter is what makes the lookup unambiguous.

        No N+1: the workload is loaded with ``select_related('brief',
        'registered_app')`` and ``prefetch_related(
        'agent_skill_refs__skill__tool_defs', 'containers')`` so the
        Brief, image, every skill, and every tool come back in a bounded
        number of queries regardless of how many skills/tools the agent
        carries.
        """
        org_pk = _caller_org_id(info, org_id)
        w = (
            _agent_workload_qs(org_pk)
            .filter(slug=slug)
            .select_related("brief", "registered_app")
            .prefetch_related("agent_skill_refs__skill__tool_defs", "containers")
            .first()
        )
        return agent_detail_to_type(w) if w is not None else None

    @strawberry.field(
        deprecation_reason=(
            "Unbounded: returns every trigger bound to the agent in one response. "
            "Use agentTriggersPage instead."
        )
    )
    @require_permission(Permission.AGENT_READ)
    @tenant_scoped()
    def agent_triggers(self, info: Info, org_id: strawberry.ID, agent_slug: str) -> list[AgentTriggerType]:
        """Inbound trigger webhooks bound to one agent (spec 33, PR-6 / #951).

        Backs the run-spec editor's Trigger card: every ``WorkflowWebhook``
        bound to the ``kind: agent`` Workload ``agent_slug`` in the caller's
        org, newest first. Includes disabled (unbound) rows so the editor can
        reflect a binding's enabled state; the signing secret is never
        surfaced. Org-scoped through ``_caller_org_id`` + the agent's org, so a
        foreign / unknown slug yields an empty list rather than another
        tenant's bindings.
        """
        org_pk = _caller_org_id(info, org_id)
        hooks = _agent_triggers_qs(org_pk, agent_slug=agent_slug).order_by("-created_at", "-slug")
        return [agent_trigger_to_type(h) for h in hooks]

    @strawberry.field
    @require_permission(Permission.AGENT_READ)
    @tenant_scoped()
    def agent_triggers_page(
        self,
        info: Info,
        org_id: strawberry.ID,
        agent_slug: str,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[AgentTriggerType]:
        """Cursor-paginated inbound triggers for one agent (#1235).

        ``agentTriggers`` hands back every binding an agent has ever had in
        one response; a repo-wide SCM fan-out accumulates them faster than
        the Trigger card can render.

        Seek key is ``(-created_at, -slug)``: ``WorkflowWebhook`` is a plain
        ``models.Model`` with no ``guid`` column, and its ``slug`` is
        ``unique=True`` + NOT NULL — which is exactly what a keyset tiebreak
        needs. ``search`` matches the slug plus the SCM ``scm_repo`` /
        ``branch_pattern`` filters the operator configured.

        Org-scoped identically to :meth:`agent_triggers`: ``org_id`` must
        match the caller's active tenant (``_caller_org_id`` raises
        otherwise) and the agent is resolved inside that org, so a foreign
        slug yields an empty page — items AND ``totalCount``.
        """
        org_pk = _caller_org_id(info, org_id)
        page = keyset_page(
            _agent_triggers_qs(org_pk, agent_slug=agent_slug, search=search),
            cursor=after,
            limit=limit,
            tiebreak_field="slug",
        )
        return page.map(agent_trigger_to_type)

    @strawberry.field
    @require_permission(Permission.AGENT_READ)
    @tenant_scoped()
    def scan_agent_manifests(
        self,
        info: Info,
        org_id: strawberry.ID,
        source_repo: str,
        source_kind: str = "github",
        ref: str = "main",
    ) -> ScanAgentManifestsResultType:
        """Scan a repo for agent manifests and return a preview (spec 33 PR-3).

        Backs the monorepo-discovery step of the agent onboarding wizard:
        given a repo handle (``source_repo`` = ``owner/name``), walks
        ``agents/*/astrolift.toml`` + a root ``astrolift.toml`` and returns
        each agent manifest as a preview row WITHOUT persisting anything.
        The operator then confirms registration via ``registerAgentRepo``.

        Read-only and org-scoped exactly like :meth:`agent_workloads`:
        ``org_id`` must match the caller's active tenant (superusers
        excepted, via ``_caller_org_id``), and the repo is fetched through
        the org's own source connection — a caller can neither scan with
        another tenant's credentials nor see another tenant's registered
        agents (``already_registered`` is computed against this org's apps).
        """
        from astrolift_registry.services.manifest_sync import discover_agent_manifests

        org_pk = _caller_org_id(info, org_id)
        result = discover_agent_manifests(
            organization_id=org_pk,
            source_kind=source_kind or "github",
            source_repo=source_repo,
            ref=ref or "main",
        )
        return ScanAgentManifestsResultType(
            ok=result.status == "ok",
            agents=[
                DiscoveredAgentManifestType(
                    manifest_path=a.manifest_path,
                    name=a.name,
                    slug=a.slug,
                    workload_kind=a.workload_kind,
                    already_registered=a.already_registered,
                )
                for a in result.agents
            ],
            error=result.error,
        )

    @strawberry.field
    @require_permission(Permission.AGENT_READ)
    @tenant_scoped()
    def agent_live_status(
        self,
        info: Info,
        org_id: strawberry.ID,
        project_slug: str | None = None,
        workload_id: strawberry.ID | None = None,
    ) -> list[AgentLiveStatusType]:
        """Per-agent live status for the caller's org, one row per agent.

        Drives the live badges on the Agents list (poll the whole
        project/fleet) and the detail header (pass ``workload_id`` to get
        just that agent). For each agent it rolls up the in-flight run
        count, the last-run status/time, the paused + idle flags, and —
        for a ``run_mode == schedule`` unpaused agent — the next cron
        firing (computed from ``run_cron_expression`` via the platform
        cron evaluator; null for non-schedule / paused agents or when no
        firing falls in the look-ahead window).

        Org-scoped exactly like :meth:`agent_workloads`. ``project_slug``
        narrows to one project; ``workload_id`` narrows to one agent
        (resolved within the org — a foreign-org or unknown GUID yields
        an empty list, never another tenant's status).
        """
        from django.utils import timezone

        from astrolift_registry.models import Workload

        org_pk = _caller_org_id(info, org_id)
        qs = _agent_workload_qs(org_pk, project_slug=project_slug)
        if workload_id:
            qs = qs.filter(guid=str(workload_id))
        workloads = list(qs[:_AGENT_LIST_CAP])
        rollup = _agent_run_rollup([w.pk for w in workloads])
        now = timezone.now()

        rows: list[AgentLiveStatusType] = []
        for w in workloads:
            stats = rollup.get(w.pk, {})
            running = stats.get("running", 0)
            # Next-scheduled only for an unpaused, schedule-mode agent —
            # the only run mode whose firing is computable from the cron
            # expression. Loop/trigger/once have no clock-derived next
            # time, so they report null (the FE renders "—" / on-demand).
            next_scheduled = None
            if (
                w.run_mode == Workload.RunMode.SCHEDULE
                and not w.run_paused
                and (w.run_cron_expression or "").strip()
            ):
                next_scheduled = _next_cron_fire(w.run_cron_expression, after=now)
            # Service-family (#1012): the Deployment IS the run — read its
            # live replica status. Best-effort + per-agent guarded so one
            # unreachable Deployment never fails the whole list.
            desired_replicas = ready_replicas = deployment_ready = None
            if w.run_family == Workload.RunFamily.SERVICE:
                desired_replicas, ready_replicas, deployment_ready = _service_replica_status(w)
            rows.append(
                AgentLiveStatusType(
                    workload_id=GUID(str(w.guid)),
                    workload_slug=w.slug,
                    app_slug=w.registered_app.slug,
                    run_family=w.run_family,
                    run_mode=w.run_mode,
                    is_paused=w.run_paused,
                    is_idle=running == 0,
                    running_count=running,
                    last_run_status=stats.get("last_status"),
                    last_run_at=stats.get("last_at"),
                    next_scheduled_at=next_scheduled,
                    desired_replicas=desired_replicas,
                    ready_replicas=ready_replicas,
                    deployment_ready=deployment_ready,
                )
            )
        return rows

    @strawberry.field
    def agent_runtimes(self, info: Info) -> list[AgentRuntimeType]:
        # Platform-level reference data: the public runtime catalog is
        # install-wide (not per-tenant) — the same 12 published images are
        # selectable by every org, so this intentionally escapes
        # @tenant_scoped (same shape as astrolift_provider_plugins /
        # form_field_types). Requires an authenticated caller inline so the
        # catalog doesn't leak to anonymous probes. See EXEMPT entry in
        # test_tenancy_guardrail.py.
        from astrolift_agents.runtime_catalog import catalog_entries

        user = getattr(getattr(info.context, "request", None), "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            raise GraphQLError("authentication required")
        return [AgentRuntimeType(name=e["name"], image=e["image"]) for e in catalog_entries()]

    @strawberry.field
    def dispatchers(self, info: Info) -> list[DispatcherInstanceType]:
        # Platform-level routing fabric: DispatcherInstances span tenants
        # (one per cluster/cloud/region), so this resolver intentionally
        # escapes @tenant_scoped — same shape as astrolift_provider_plugins.
        # Staff/superuser only; the api_key_hash is never surfaced (the
        # type omits it). See EXEMPT entry in test_tenancy_guardrail.py.
        user = getattr(getattr(info.context, "request", None), "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            raise GraphQLError("authentication required")
        if not (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)):
            raise GraphQLError("staff access required")
        qs = DispatcherInstance.objects.filter(deleted_at__isnull=True).order_by("slug")[:200]
        return [dispatcher_to_type(d) for d in qs]
