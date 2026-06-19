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

from typing import Any

import strawberry
from graphql import GraphQLError
from strawberry.types import Info

from astrolift_agents.models import (
    AgentEnvironmentSpec,
    AgentTask,
    Brief,
    DispatcherInstance,
    Skill,
    ToolDef,
)
from astrolift_agents.schema.types import (
    AgentEnvironmentSpecType,
    AgentListItemType,
    AgentLiveStatusType,
    AgentRuntimeType,
    AgentTaskType,
    BriefType,
    DiscoveredAgentManifestType,
    DispatcherInstanceType,
    ScanAgentManifestsResultType,
    SkillType,
    ToolDefType,
    agent_env_spec_to_type,
    agent_task_to_type,
    brief_to_type,
    dispatcher_to_type,
    skill_to_type,
    tool_def_to_type,
)
from astrolift_graphql import GUID
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


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
        # select_related the org so snapshot_url presigning (per RUNNING vnc
        # row) doesn't fire a query per task — the org is the only related
        # object agent_task_to_type touches.
        qs = qs.select_related("organization").order_by("-created_at")[:200]
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
            .select_related("organization")
            .order_by("-started_at", "-created_at")[:200]
        )
        return [agent_task_to_type(t) for t in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def agent_task(self, info: Info, id: strawberry.ID) -> AgentTaskType | None:
        """One AgentTask by GUID, scoped to the caller's org."""
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        row = (
            AgentTask.objects.filter(guid=str(id), organization_id=org_pk, deleted_at__isnull=True)
            .select_related("organization")
            .first()
        )
        return agent_task_to_type(row) if row is not None else None

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

        Note on pod discovery: the K8s Job spawner labels each agent pod
        ``astrolift.dev/task-id=<task.guid>`` and the agent namespace is
        ``astrolift-agents-<org-slug>`` (see
        ``astrolift_workflows.activities.agent_stage``). The default live
        pod backend selects on the ``astrolift.dev/app`` label, so on a
        real cluster the discovery falls through to the recorded Job name
        on ``AgentTask.pod_name``; wiring a task-id label selector into
        the driver SDK is the follow-up that makes live discovery exact.
        """
        from asgiref.sync import async_to_sync

        from core.cluster_observability import fetch_task_pod_logs

        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        if org_pk is None:
            return []

        # Resolve the task + its dispatcher's cluster off the event loop
        # (Django ORM is sync). Returns the data the async log fetch
        # needs, or None when there is no readable pod to target.
        def _resolve() -> tuple[Any, str, str, str] | None:
            row = (
                AgentTask.objects.filter(
                    guid=str(id), organization_id=org_pk, deleted_at__isnull=True
                )
                .select_related("organization", "dispatcher", "dispatcher__tenant_cluster")
                .first()
            )
            if row is None:
                return None
            dispatcher = row.dispatcher
            cluster = dispatcher.tenant_cluster if dispatcher is not None else None
            if cluster is None or not getattr(cluster, "is_active", True):
                return None
            org_slug = (getattr(row.organization, "slug", "") or "").strip()
            if not org_slug:
                return None
            # The spawner runs agent Jobs in the per-org agent namespace
            # (astrolift_workflows.activities.agent_stage._agent_namespace).
            namespace = f"astrolift-agents-{org_slug}"
            return cluster, namespace, str(row.guid), (row.pod_name or "")

        resolved = _resolve()
        if resolved is None:
            return []
        cluster, namespace, task_guid, pod_name_hint = resolved

        # The /app/gql GraphQL view runs sync (threadpool, no event loop), so
        # bridge the async pod-log fetch with async_to_sync rather than making
        # the resolver async (which the sync view can't drive).
        return async_to_sync(fetch_task_pod_logs)(
            cluster=cluster,
            namespace=namespace,
            task_guid=task_guid,
            pod_name_hint=pod_name_hint,
            tail=tail,
        )

    @strawberry.field
    @require_permission(Permission.APP_READ)
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
    @require_permission(Permission.APP_READ)
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
                )
            )
        return rows

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
        return self.agent_workloads(info, org_id=org_id, project_slug=None)

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
