"""Read-only queries for the Agent Dispatch Layer.

Skills, ToolDefs, Briefs and org skill repos are the org's catalog and
check at the explicit org scope. AgentTasks, boxes and environment specs
are owned by a project, a team or an agent's app and narrow to the rows the
caller's grants reach (#1866). The ``dispatchers`` resolver is
platform-level (the routing fabric spans tenants) and is the platform
operator's alone (#1978).

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
from django.db.models import Case, Q, Value, When
from django.db.models.functions import Lower
from graphql import GraphQLError
from strawberry.types import Info

from astrolift_agents.models import (
    AgentBox,
    AgentEnvironmentSpec,
    AgentInteraction,
    AgentSecretBundleRef,
    AgentTask,
    AgentTaskEvent,
    AgentTaskInputMessage,
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
    AgentFleetFilterInput,
    AgentInteractionType,
    AgentListItemPageType,
    AgentListItemType,
    AgentLiveStatusType,
    AgentRuntimeType,
    AgentSecretBundleAttachmentType,
    AgentSecretBundleType,
    AgentSecretStatusFilterInput,
    AgentSecretStatusPageType,
    AgentSecretStatusType,
    AgentTaskEventType,
    AgentTaskInputMessageType,
    AgentTaskPageType,
    AgentTasksFilterInput,
    AgentTaskType,
    AgentTriggerType,
    AgentUpcomingRunPageType,
    AgentUpcomingRunType,
    BriefType,
    DiscoveredAgentManifestType,
    DispatcherInstanceType,
    OrgSkillRepoType,
    ScanAgentManifestsResultType,
    SkillPageType,
    SkillsFilterInput,
    SkillType,
    ToolDefPageType,
    ToolDefsFilterInput,
    ToolDefType,
    agent_box_to_type,
    agent_detail_to_type,
    agent_env_spec_to_type,
    agent_interaction_to_type,
    agent_secret_bundle_attachment_to_type,
    agent_secret_bundle_to_type,
    agent_secret_status_to_type,
    agent_task_input_message_to_type,
    agent_task_to_type,
    agent_tasks_to_types,
    agent_trigger_to_type,
    brief_to_type,
    dispatcher_to_type,
    org_skill_repo_to_type,
    skill_to_type,
    tool_def_to_type,
)
from astrolift_agents.scopes import (
    agent_box_scope,
    agent_org_scope,
    agent_task_scope,
    agent_workload_app_scope,
)
from astrolift_agents.visibility import agent_boxes as visible_agent_boxes
from astrolift_agents.visibility import agent_tasks as visible_agent_tasks
from astrolift_agents.visibility import agent_workloads as visible_agent_workloads
from astrolift_agents.visibility import dispatchable_agent_workloads
from astrolift_agents.visibility import environment_specs as visible_environment_specs
from astrolift_graphql import (
    DEFAULT_PAGE_SIZE,
    GUID,
    MAX_PAGE_LIMIT,
    FilterField,
    PageType,
    SortKey,
    UnsupportedSort,
    clamp_limit,
    filter_q,
    filter_values,
    keyset_page,
    numbered_page,
    parse_sort_spec,
    resolve_list_sort,
    search_q,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission, require_platform_operator
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


def _agent_workload_qs(org_pk: int, *, project_slug: str | None = None, dispatchable: bool = False):
    """Base queryset of the caller-org's ``kind: agent`` workloads.

    Permission and token/share filters precede the optional project
    filter, ordering and result cap. Deleted workloads/apps are excluded.

    ``dispatchable=True`` (#2071) swaps the ``agent.read`` visibility set for
    :func:`~astrolift_agents.visibility.dispatchable_agent_workloads` --
    ``agent.dispatch``-gated, token-ceilinged, Task-family only -- so the
    list matches exactly what ``runAstroliftAgent`` would accept.
    """
    qs = dispatchable_agent_workloads(org_pk) if dispatchable else visible_agent_workloads(org_pk)
    qs = qs.select_related(
        "registered_app", "registered_app__project", "created_by", "registered_app__created_by"
    )
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

    matches = list(_agent_workload_qs(org_pk).filter(slug=agent_slug)[:2])
    if len(matches) != 1:
        return WorkflowWebhook.objects.none()
    workload = matches[0]
    qs = WorkflowWebhook.objects.filter(
        agent_definition=workload,
        organization_id=org_pk,
    ).select_related("organization")
    if search:
        qs = qs.filter(search_q(search, "slug", "scm_repo", "branch_pattern"))
    return qs


def _agent_list_rows(
    info: Info, org_id: strawberry.ID, project_slug: str | None, *, dispatchable: bool = False
) -> list[AgentListItemType]:
    """Build the agent list rows for an org (+ optional project filter).

    Module-level so both ``agent_workloads`` and ``agent_fleet`` can call it.
    The fleet resolver previously did ``self.agent_workloads(...)``, but
    Strawberry passes the root value (``None``) as ``self`` on a field
    resolver, so that raised ``'NoneType' has no attribute 'agent_workloads'``.
    """
    org_pk = _caller_org_id(info, org_id)
    workloads = list(
        _agent_workload_qs(org_pk, project_slug=project_slug, dispatchable=dispatchable)[:_AGENT_LIST_CAP]
    )
    return _agent_list_rows_for_workloads(workloads)


def _agent_list_rows_for_workloads(workloads) -> list[AgentListItemType]:
    """List rows for ``workloads``, with every column fetched in bulk.

    One query per source regardless of the page size: the run rollup (two),
    the environment specs that share the agents' slugs, and the clusters
    their apps' environments sit on. Owners ride on the workload query
    (``_agent_workload_qs`` select_relates both creators).
    """
    workloads = list(workloads)
    rollup = _agent_run_rollup([w.pk for w in workloads])
    specs = _fleet_specs(workloads)
    clusters = _fleet_clusters(workloads)
    viewer = _viewer_id()
    rows: list[AgentListItemType] = []
    for w in workloads:
        stats = rollup.get(w.pk, {})
        app = w.registered_app
        spec = specs.get((app.organization_id, w.slug))
        owner = w.created_by if w.created_by_id is not None else app.created_by
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
                status=getattr(w, "_fleet_status", None) or _fleet_status(w, stats),
                model_source=_model_source(spec),
                runtime=(spec.runtime or "") if spec is not None else "",
                environment_spec_slug=spec.slug if spec is not None else "",
                cluster_slugs=clusters.get(app.pk, []),
                owner_email=(getattr(owner, "email", "") or "") if owner is not None else "",
                owned_by_me=owner is not None and viewer is not None and owner.pk == viewer,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# The list contract on the Agents list (spec 44 §5.1, #2155)
# ---------------------------------------------------------------------------
#
# Everything the Agents screen used to join in the browser (status, model,
# runtime, cluster, owner) is a column or an annotation here, so a numbered
# page's OFFSET and totalCount are exact. The status is the screen's own
# ``agentStatusKey``, in SQL: running beats paused beats a failed last run
# beats a schedule beats idle. "scheduled" is a schedule-mode agent with a
# cron expression; an expression the cron parser rejects still reads as
# scheduled here, and ``agentLiveStatus`` reports no next firing for it.

_FLEET_STATUS_ORDER = ["running", "failing", "scheduled", "paused", "idle"]


def _viewer_id() -> int | None:
    tenant = get_current_tenant()
    return tenant.actor_user_id if tenant else None


def _user_ids(values, viewer_id: int | None) -> list[int]:
    """``["me", "12"]`` as user pks; "me" is the viewer, junk is dropped."""
    out: list[int] = []
    for value in values if isinstance(values, list) else [values]:
        if value == "me":
            if viewer_id is not None:
                out.append(viewer_id)
            continue
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


def _iexact_any(path: str, values) -> Q:
    """Case-insensitive "is one of"; an empty list matches nothing."""
    query = Q(pk__in=[])
    for value in values if isinstance(values, list) else [values]:
        query |= Q(**{f"{path}__iexact": value})
    return query


def _fleet_status(w, stats: dict) -> str:
    """The row status from the Python rollup, for the unannotated lists."""
    from astrolift_lifecycle.models import AgentRun
    from astrolift_registry.models import Workload

    if stats.get("running", 0) > 0:
        return "running"
    if w.run_paused:
        return "paused"
    if stats.get("last_status") == AgentRun.Status.FAILED:
        return "failing"
    if w.run_mode == Workload.RunMode.SCHEDULE and (w.run_cron_expression or "").strip():
        return "scheduled"
    return "idle"


def _model_source(spec) -> str | None:
    if spec is None:
        return None
    if spec.managed_model:
        return "managed"
    if spec.model_gateway:
        return "gateway"
    return "api-key"


def _fleet_specs(workloads) -> dict[tuple[int, str], AgentEnvironmentSpec]:
    """The environment spec that shares each agent's slug, keyed ``(org, slug)``."""
    orgs = {w.registered_app.organization_id for w in workloads}
    slugs = {w.slug for w in workloads}
    if not orgs:
        return {}
    rows = AgentEnvironmentSpec.objects.filter(
        organization_id__in=orgs, slug__in=slugs, deleted_at__isnull=True
    ).only("organization_id", "slug", "runtime", "managed_model", "model_gateway")
    return {(r.organization_id, r.slug): r for r in rows}


def _fleet_clusters(workloads) -> dict[int, list[str]]:
    """Distinct cluster slugs per app, in environment-name order, in one query."""
    from astrolift_lifecycle.models import AppEnvironment

    app_ids = {w.registered_app_id for w in workloads}
    if not app_ids:
        return {}
    rows = (
        AppEnvironment.objects.filter(
            registered_app_id__in=app_ids, deleted_at__isnull=True, tenant_cluster__isnull=False
        )
        .order_by("registered_app_id", "name", "pk")
        .values_list("registered_app_id", "tenant_cluster__slug")
    )
    out: dict[int, list[str]] = {}
    for app_id, slug in rows:
        seen = out.setdefault(app_id, [])
        if slug not in seen:
            seen.append(slug)
    return out


def _annotate_fleet(qs, org_pk: int):
    """Annotate the columns the Agents list filters and sorts on."""
    from django.db.models import CharField, Exists, IntegerField, OuterRef, Subquery
    from django.db.models.functions import Coalesce

    from astrolift_lifecycle.models import AgentRun
    from astrolift_registry.models import Workload

    runs = AgentRun.objects.filter(workload=OuterRef("pk")).order_by("-created_at", "-pk")
    specs = AgentEnvironmentSpec.objects.filter(
        organization_id=org_pk, slug=OuterRef("slug"), deleted_at__isnull=True
    ).order_by("pk")
    qs = qs.annotate(
        _last_run_status=Subquery(runs.values("status")[:1]),
        _last_run_at=Subquery(runs.annotate(_at=Coalesce("started_at", "created_at")).values("_at")[:1]),
        _running=Exists(AgentRun.objects.filter(workload=OuterRef("pk"), status=AgentRun.Status.RUNNING)),
        _spec_runtime=Subquery(specs.values("runtime")[:1]),
        _model_source=Subquery(
            specs.annotate(
                _src=Case(
                    When(managed_model=True, then=Value("managed")),
                    When(model_gateway=True, then=Value("gateway")),
                    default=Value("api-key"),
                    output_field=CharField(),
                )
            ).values("_src")[:1]
        ),
    )
    qs = qs.annotate(
        _fleet_status=Case(
            When(_running=True, then=Value("running")),
            When(run_paused=True, then=Value("paused")),
            When(_last_run_status=AgentRun.Status.FAILED, then=Value("failing")),
            When(
                Q(run_mode=Workload.RunMode.SCHEDULE) & ~Q(run_cron_expression=""),
                then=Value("scheduled"),
            ),
            default=Value("idle"),
            output_field=CharField(),
        )
    )
    return qs.annotate(
        _status_rank=Case(
            *(
                When(_fleet_status=status, then=Value(rank))
                for rank, status in enumerate(_FLEET_STATUS_ORDER)
            ),
            default=Value(len(_FLEET_STATUS_ORDER)),
            output_field=IntegerField(),
        )
    )


def _agents_on_clusters(slugs) -> Q:
    from astrolift_lifecycle.models import AppEnvironment

    envs = AppEnvironment.objects.filter(_iexact_any("tenant_cluster__slug", slugs), deleted_at__isnull=True)
    return Q(registered_app_id__in=envs.values("registered_app_id"))


def _runtime_q(values) -> Q:
    return _iexact_any("_spec_runtime", values) | _iexact_any("run_family", values)


_FLEET_SORTS: dict[str, SortKey] = {
    "name": SortKey(Lower("name")),
    "slug": SortKey("slug"),
    "status": SortKey("_status_rank"),
    # Never run sorts below the oldest run, as it did in the browser.
    "lastRun": SortKey("_last_run_at", nulls_low=True),
    "project": SortKey(Lower("registered_app__project__slug"), nulls_low=True),
    "created": SortKey("created_at"),
}

_FLEET_FILTERS: dict[str, FilterField] = {
    "project": FilterField(q=lambda v: _iexact_any("registered_app__project__slug", v)),
    "status": FilterField("_fleet_status"),
    "model": FilterField("_model_source"),
    "runtime": FilterField(q=_runtime_q),
    "cluster": FilterField(q=_agents_on_clusters),
    "paused": FilterField("run_paused"),
    # ``owner`` needs the viewer; the resolver applies it.
}


def _fleet_owner_q(values, viewer_id: int | None) -> Q:
    ids = _user_ids(values, viewer_id)
    return Q(created_by_id__in=ids) | Q(created_by__isnull=True, registered_app__created_by_id__in=ids)


def _fleet_search_q(search: str) -> Q:
    return search_q(
        search,
        "name",
        "slug",
        "registered_app__slug",
        "registered_app__project__slug",
        "registered_app__source_repo",
    )


# ---------------------------------------------------------------------------
# agentTasksPage: filters and sort (#2155)
# ---------------------------------------------------------------------------

_TASK_FILTERS: dict[str, FilterField] = {
    "status": FilterField("status"),
    "trigger": FilterField("trigger_kind"),
    "agent": FilterField(q=lambda v: _iexact_any("agent_definition__slug", v)),
    "project": FilterField(q=lambda v: _iexact_any("project__slug", v)),
    # ``started_by`` / ``started_by_me`` need the viewer; the resolver applies them.
}

#: A cursor list sorts on one NOT NULL key (README, "Multi-key sort on a
#: cursor list is not built yet"): created or updated, either direction.
_TASK_SORT_FIELDS = {"created": "created_at", "updated": "updated_at"}


def _task_sort(sort: str | None, org_pk: int) -> tuple[str, bool, str]:
    """``(sort_field, descending, cursor_scope)``; newest created first by default.

    The default keeps the scope the page has always issued, so a cursor from
    before ``sort`` existed keeps walking; any other order is scoped apart.
    """
    pairs = parse_sort_spec(sort) or [("created", True)]
    if len(pairs) > 1 or pairs[0][0] not in _TASK_SORT_FIELDS:
        raise UnsupportedSort(f"sort {sort!r} is not available on agent tasks; supported: created, updated")
    key, descending = pairs[0]
    scope = f"agent-tasks:{org_pk}"
    if (key, descending) != ("created", True):
        scope = f"{scope}:{'-' if descending else ''}{key}"
    return _TASK_SORT_FIELDS[key], descending, scope


def _apply_task_filter(qs, values: dict, viewer_id: int | None):
    qs = qs.filter(filter_q(values, _TASK_FILTERS))
    if "started_by" in values:
        qs = qs.filter(triggered_by_user_id__in=_user_ids(values["started_by"], viewer_id))
    mine = values.get("started_by_me")
    if mine is True:
        qs = qs.filter(triggered_by_user_id=viewer_id) if viewer_id is not None else qs.none()
    elif mine is False and viewer_id is not None:
        qs = qs.filter(Q(triggered_by_user_id__isnull=True) | ~Q(triggered_by_user_id=viewer_id))
    return qs


# ---------------------------------------------------------------------------
# Upcoming scheduled runs (#2155)
# ---------------------------------------------------------------------------

#: Firings per agent the upcoming-runs list will compute.
_UPCOMING_PER_AGENT_MAX = 10


def _upcoming_firings(expression: str, *, after, count: int, until=None) -> list:
    """The next ``count`` firings of a valid ``expression`` after ``after``."""
    out = []
    cursor = after
    for _ in range(count):
        nxt = _next_cron_fire(expression, after=cursor)
        if nxt is None or (until is not None and nxt > until):
            break
        out.append(nxt)
        cursor = nxt
    return out


# ---------------------------------------------------------------------------
# Catalog lists: skills and tool definitions (#2155)
# ---------------------------------------------------------------------------


def _scope_q(values, *, org_field: str, global_field: str, org_pk: int) -> Q:
    query = Q(pk__in=[])
    for value in values if isinstance(values, list) else [values]:
        if value == "org":
            query |= Q(**{org_field: org_pk})
        elif value == "global":
            query |= Q(**{global_field: True})
    return query


_SKILL_SORTS: dict[str, SortKey] = {
    "name": SortKey(Lower("name")),
    "slug": SortKey("slug"),
    "created": SortKey("created_at"),
    "updated": SortKey("updated_at"),
    "version": SortKey("skill_version"),
}

_SKILL_FILTERS: dict[str, FilterField] = {
    "active": FilterField("is_active"),
    "source_kind": FilterField("source_kind"),
    "agent_type": FilterField("agent_type"),
    # ``scope``, ``imported`` and ``created_by`` are applied by the resolver.
}

_TOOL_SORTS: dict[str, SortKey] = {
    "name": SortKey(Lower("name")),
    "slug": SortKey("slug"),
    "skill": SortKey(Lower("skill__slug")),
    "adapter": SortKey("adapter"),
    "created": SortKey("created_at"),
}

_TOOL_FILTERS: dict[str, FilterField] = {
    "skill": FilterField(q=lambda v: _iexact_any("skill__slug", v)),
    "adapter": FilterField("adapter"),
    "builtin": FilterField("is_builtin"),
    "capability_group": FilterField(q=lambda v: _iexact_any("capability_group", v)),
    # ``scope`` and ``created_by`` are applied by the resolver.
}

# ---------------------------------------------------------------------------
# Secret status page (#2155)
# ---------------------------------------------------------------------------

_SECRET_STATUS_SORTS = {
    "envVar": lambda r: (r.env_var or "").lower(),
    "uri": lambda r: (r.uri or "").lower(),
    "exists": lambda r: r.exists,
    "provider": lambda r: (r.provider or "").lower(),
}


def _sorted_rows(rows: list, sort: str | None, keys: dict, *, default: str, list_name: str) -> list:
    """Sort rows only Python holds (a secret-store probe) by a declared spec.

    Stable sorts applied from the last key to the first give the multi-key
    order; an undeclared key is refused exactly as ``resolve_list_sort``
    refuses one.
    """
    pairs = parse_sort_spec(sort) or parse_sort_spec(default)
    unknown = [key for key, _ in pairs if key not in keys]
    if unknown:
        offered = ", ".join(sorted(keys))
        raise UnsupportedSort(f"sort {unknown[0]!r} is not available on {list_name}; supported: {offered}")
    out = list(rows)
    for key, descending in reversed(pairs):
        out.sort(key=keys[key], reverse=descending)
    return out


def _slice_page(rows: list, page: int | None, page_size: int | None) -> tuple[list, int, int]:
    """``(rows on the page, page, page_size)`` with ``numbered_page``'s clamping."""
    size = clamp_limit(page_size, default=DEFAULT_PAGE_SIZE, maximum=MAX_PAGE_LIMIT)
    number = page if page is not None and page > 0 else 1
    start = (number - 1) * size
    return rows[start : start + size], number, size


@strawberry.type
class AgentsQuery:
    @strawberry.field
    @require_permission(Permission.APP_READ, scope=agent_org_scope)
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
        qs = (
            Skill.objects.filter(scope, deleted_at__isnull=True)
            .select_related("created_by")
            .order_by("-is_global", "slug")[:200]
        )
        return [skill_to_type(s) for s in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ, scope=agent_org_scope)
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
    @require_permission(Permission.APP_READ, scope=agent_org_scope)
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
        qs = (
            ToolDef.objects.filter(skill=skill, deleted_at__isnull=True)
            .select_related("skill", "created_by")
            .order_by("slug")[:200]
        )
        return [tool_def_to_type(t) for t in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ, scope=agent_org_scope)
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
            .select_related("skill", "created_by")
            .order_by("skill__slug", "slug")[:500]
        )
        return [tool_def_to_type(t) for t in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ, scope=agent_org_scope)
    @tenant_scoped()
    def skills_page(
        self,
        info: Info,
        org_id: strawberry.ID,
        search: str | None = None,
        filter: SkillsFilterInput | None = None,
        sort: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> SkillPageType:
        """The org's skills plus the global catalog, on the list contract (#2155).

        The numbered sibling of ``skills`` without its 200-row cap.
        ``filter``: scope (org, global), active, imported, sourceKind,
        createdBy (user ids or "me", the Mine view) and agentType.
        ``search`` matches the name, slug, description and import source;
        ``sort`` is a multi-key spec over name, slug, created, updated and
        version (``name`` by default).

        Org-scoped like ``skills``: ``orgId`` must be the caller's org
        (``_caller_org_id`` raises otherwise) and the rows are that org's
        own plus the global catalog, never another org's.
        """
        org_pk = _caller_org_id(info, org_id)
        qs = Skill.objects.filter(
            Q(organization_id=org_pk) | Q(is_global=True), deleted_at__isnull=True
        ).select_related("created_by")
        if search and search.strip():
            qs = qs.filter(search_q(search.strip(), "name", "slug", "description", "source_ref"))
        values = filter_values(filter)
        qs = qs.filter(filter_q(values, _SKILL_FILTERS))
        if "scope" in values:
            qs = qs.filter(
                _scope_q(
                    values["scope"], org_field="organization_id", global_field="is_global", org_pk=org_pk
                )
            )
        if values.get("imported") is True:
            qs = qs.exclude(source_kind="")
        elif values.get("imported") is False:
            qs = qs.filter(source_kind="")
        if "created_by" in values:
            qs = qs.filter(created_by_id__in=_user_ids(values["created_by"], _viewer_id()))
        order_by = resolve_list_sort(sort, _SKILL_SORTS, default="name")
        result = numbered_page(qs, order_by=order_by, page=page, page_size=page_size)
        return SkillPageType(
            items=[skill_to_type(row) for row in result.rows],
            total_count=result.total_count,
            page=result.page,
            page_size=result.page_size,
        )

    @strawberry.field
    @require_permission(Permission.APP_READ, scope=agent_org_scope)
    @tenant_scoped()
    def org_tool_defs_page(
        self,
        info: Info,
        org_id: strawberry.ID,
        search: str | None = None,
        filter: ToolDefsFilterInput | None = None,
        sort: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> ToolDefPageType:
        """Every tool on a skill the org can read, on the list contract (#2155).

        The numbered sibling of ``orgToolDefs`` without its 500-row cap. A
        tool whose skill is deleted is left out. ``filter``: skill (slugs),
        adapter, builtin, capabilityGroup, scope (org, global: the parent
        skill's) and createdBy (user ids or "me"). ``search`` matches the
        name, slug, description, handler and skill slug; ``sort`` is a
        multi-key spec over name, slug, skill, adapter and created.

        Org-scoped like ``orgToolDefs``: the parent skill is the org's own
        or global, and ``orgId`` must be the caller's org.
        """
        org_pk = _caller_org_id(info, org_id)
        qs = ToolDef.objects.filter(
            Q(skill__organization_id=org_pk) | Q(skill__is_global=True),
            deleted_at__isnull=True,
            skill__deleted_at__isnull=True,
        ).select_related("skill", "created_by")
        if search and search.strip():
            qs = qs.filter(
                search_q(search.strip(), "name", "slug", "description", "handler_ref", "skill__slug")
            )
        values = filter_values(filter)
        qs = qs.filter(filter_q(values, _TOOL_FILTERS))
        if "scope" in values:
            qs = qs.filter(
                _scope_q(
                    values["scope"],
                    org_field="skill__organization_id",
                    global_field="skill__is_global",
                    org_pk=org_pk,
                )
            )
        if "created_by" in values:
            qs = qs.filter(created_by_id__in=_user_ids(values["created_by"], _viewer_id()))
        order_by = resolve_list_sort(sort, _TOOL_SORTS, default="skill,name")
        result = numbered_page(qs, order_by=order_by, page=page, page_size=page_size)
        return ToolDefPageType(
            items=[tool_def_to_type(row) for row in result.rows],
            total_count=result.total_count,
            page=result.page,
            page_size=result.page_size,
        )

    @strawberry.field
    @require_permission(Permission.APP_READ, scope=agent_org_scope)
    @tenant_scoped()
    def tool_def(self, info: Info, id: strawberry.ID) -> ToolDefType | None:
        """One tool by GUID, on a skill of the caller's org or the global
        catalog (#2155). Another org's tool, a tool on a deleted skill and a
        malformed id all resolve to null, so the surface leaks no existence."""
        guid = _valid_guid(id)
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        if guid is None or org_pk is None:
            return None
        row = (
            ToolDef.objects.filter(
                Q(skill__organization_id=org_pk) | Q(skill__is_global=True),
                guid=guid,
                deleted_at__isnull=True,
                skill__deleted_at__isnull=True,
            )
            .select_related("skill", "created_by")
            .first()
        )
        return tool_def_to_type(row) if row is not None else None

    @strawberry.field
    @require_permission(Permission.APP_READ, scope=agent_org_scope)
    @tenant_scoped()
    def brief(self, info: Info, id: strawberry.ID) -> BriefType | None:
        """One Brief by GUID, scoped to the caller's org."""
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        row = Brief.objects.filter(guid=str(id), organization_id=org_pk, deleted_at__isnull=True).first()
        return brief_to_type(row) if row is not None else None

    @strawberry.field
    @require_permission(Permission.SCM_READ, scope=agent_org_scope)
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
    @require_permission(Permission.AGENT_READ, any_scope=True)
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
        qs = visible_agent_tasks(org_pk, Permission.AGENT_READ)
        if status:
            qs = qs.filter(status=status)
        if workload_id:
            workload_guid = _valid_guid(workload_id)
            if workload_guid is None:
                return []
            # Resolve the workload inside the caller's org (its app's
            # organization must match) so a foreign-org GUID can't be
            # used to filter — and so a no-match returns [] instead of
            # the org's entire task list (an unfiltered filter).
            from astrolift_registry.models import Workload

            wl = (
                Workload.objects.filter(
                    guid=workload_guid,
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
        return agent_tasks_to_types(qs)

    @strawberry.field
    @require_permission(Permission.AGENT_TASK_WATCH, any_scope=True)
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
            visible_agent_tasks(org_pk, Permission.AGENT_TASK_WATCH)
            .filter(
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
        return agent_tasks_to_types(qs)

    @strawberry.field
    @require_permission(Permission.APP_READ, scope=agent_task_scope("id", Permission.APP_READ))
    @tenant_scoped()
    def agent_task(self, info: Info, id: strawberry.ID) -> AgentTaskType | None:
        """One AgentTask by GUID, scoped to the caller's org."""
        guid = _valid_guid(id)
        if guid is None:
            return None
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        row = (
            visible_agent_tasks(org_pk, Permission.APP_READ)
            .filter(guid=guid, organization_id=org_pk)
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
    @require_permission(Permission.AGENT_READ, any_scope=True)
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
        qs = visible_agent_tasks(org_pk, Permission.AGENT_READ)
        if since is not None:
            qs = qs.filter(updated_at__gt=since)
        qs = qs.select_related(
            "organization",
            "project",
            "agent_definition",
            "dispatcher",
            "dispatcher__tenant_cluster",
        ).order_by("updated_at")[:capped]
        return agent_tasks_to_types(qs)

    @strawberry.field
    @require_permission(
        Permission.AGENT_TASK_SEND_INPUT, scope=agent_task_scope("task_id", Permission.AGENT_TASK_SEND_INPUT)
    )
    @tenant_scoped()
    def agent_task_input_message(
        self, info: Info, task_id: strawberry.ID, client_request_id: str
    ) -> AgentTaskInputMessageType | None:
        """Recover one enqueue receipt without adding or consuming input."""
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        task_guid, request_guid = _valid_guid(task_id), _valid_guid(client_request_id)
        if org_pk is None or task_guid is None or request_guid is None:
            return None
        task = (
            visible_agent_tasks(org_pk, Permission.AGENT_TASK_SEND_INPUT)
            .filter(guid=task_guid, organization_id=org_pk)
            .first()
        )
        if task is None:
            return None
        row = AgentTaskInputMessage.objects.filter(
            organization_id=org_pk, agent_task=task, client_request_id=request_guid
        ).first()
        return agent_task_input_message_to_type(row) if row else None

    @strawberry.field
    @require_permission(Permission.AGENT_READ, any_scope=True)
    @tenant_scoped()
    def agent_task_by_client_request_id(
        self, info: Info, org_id: strawberry.ID, client_request_id: str
    ) -> AgentTaskType | None:
        """Recover the task ``runAstroliftAgent`` created for a
        ``clientRequestId``, without dispatching anything (#2072).

        A caller that launched an agent and crashed before it recorded
        ``runAstroliftAgent``'s reply cannot otherwise tell whether the task
        exists -- retrying the mutation is guarded by the same key
        (``dispatch_registered_agent``'s idempotency check), but a client
        that lost the reply needs a read, not another dispatch attempt.

        The key is scoped to the requester who minted it (matching
        ``dispatch_registered_agent``'s ``created_by`` scoping): this
        resolves only a task ``created_by`` the CALLER's own actor id, never
        another org member's, even one holding ``agent.read`` -- a
        clientRequestId is not itself a secret, so a coarse org-wide lookup
        would let any reader guess-and-check another user's key. Returns
        null for a malformed key, a key from another org, one the caller
        cannot read, or one that exists but belongs to a different
        requester -- same "null, never leak existence" shape as
        ``agentTaskInputMessage``.
        """
        org_pk = _caller_org_id(info, org_id)
        request_guid = _valid_guid(client_request_id)
        if request_guid is None:
            return None
        tenant = get_current_tenant()
        requester_id = tenant.actor_user_id if tenant else None
        task = (
            visible_agent_tasks(org_pk, Permission.AGENT_READ)
            .filter(organization_id=org_pk, created_by_id=requester_id, client_request_id=request_guid)
            .first()
        )
        return agent_task_to_type(task) if task is not None else None

    @strawberry.field
    @require_permission(Permission.AGENT_READ, scope=agent_task_scope("task_id"))
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
            visible_agent_tasks(org_pk, Permission.AGENT_READ)
            .filter(guid=guid, organization_id=org_pk)
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
    @require_permission(Permission.AGENT_READ, scope=agent_task_scope("task_id"))
    @tenant_scoped()
    def agent_task_events(
        self,
        info: Info,
        org_id: strawberry.ID,
        task_id: strawberry.ID,
        after: int = 0,
        limit: int = 100,
    ) -> list[AgentTaskEventType]:
        org_pk = _caller_org_id(info, org_id)
        guid = _valid_guid(task_id)
        if guid is None:
            return []
        task = (
            visible_agent_tasks(org_pk, Permission.AGENT_READ)
            .filter(
                guid=guid,
                organization_id=org_pk,
            )
            .first()
        )
        if task is None:
            return []
        if after < 0 or after > task.event_sequence:
            raise GraphQLError("Invalid task event cursor")
        rows = list(
            AgentTaskEvent.objects.filter(
                organization_id=org_pk,
                agent_task=task,
                sequence__gt=after,
                sequence__lte=task.event_sequence,
            ).order_by("sequence")[: max(1, min(limit, 100))]
        )
        if (not rows and after < task.event_sequence) or any(
            row.sequence != after + index + 1 for index, row in enumerate(rows)
        ):
            raise GraphQLError("Task event history is incomplete; refresh the session history")
        return [
            AgentTaskEventType(
                sequence=row.sequence,
                turn_id=row.turn_id,
                message_id=row.message_id,
                kind=row.kind,
                text=row.text,
                created_at=row.created_at,
                request=row.request,
                data=row.data,
            )
            for row in rows
        ]

    @strawberry.field
    @require_permission(Permission.AGENT_READ, scope=agent_task_scope("id"))
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

        Requires ``agent.read`` at the task's effective ownership scope.

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
        ``AgentTask.pod_name`` is a last-resort fallback, re-queried as a
        ``job_name`` selector against Kubernetes' own ``job-name`` label
        rather than assumed to be the pod's own name (#1712).
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
                visible_agent_tasks(org_pk, Permission.AGENT_READ)
                .filter(guid=guid, organization_id=org_pk)
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
            # Agent-task Jobs set no ttlSecondsAfterFinished (only agent-box,
            # build, and pipeline Jobs do) -- nothing here garbage-collects a
            # completed pod on a timer. A missing pod means either an
            # operator/cancellation stop already deleted the Job, or the two
            # label-based discovery attempts (task-id, then the Job's own
            # job-name) both missed.
            _empty(
                f"no pod logs found on cluster={getattr(cluster, 'slug', '?')} "
                f"namespace={namespace} pod_hint={pod_name_hint or '(none)'} — "
                f"the pod may have been deleted by an explicit stop/cancel"
            )
        return lines

    @strawberry.field
    @require_permission(Permission.AGENT_ENV_SPEC_READ, any_scope=True)
    @tenant_scoped()
    def agent_environment_specs(self, info: Info, org_id: strawberry.ID) -> list[AgentEnvironmentSpecType]:
        """The environment specs the caller may read, ordered by slug.

        Org-scoped: ``org_id`` must match the caller's active tenant
        (superusers excepted) — the spec carries secret *references* the
        dispatcher resolves at launch, so it must never leak across orgs.
        Within the org the rows narrow to the org-shared specs and the ones
        owned by a project or team the caller's grants cover (#1866).
        """
        org_pk = _caller_org_id(info, org_id)
        qs = visible_environment_specs(org_pk, Permission.AGENT_ENV_SPEC_READ).order_by("slug")[:200]
        return [agent_env_spec_to_type(s) for s in qs]

    @strawberry.field
    @require_permission(Permission.AGENT_READ, any_scope=True)
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

        Rows narrow to the boxes the caller's grants reach: a box belongs to
        its recorded project or team, else its agent's app, else the org
        (#1866).
        """
        org_pk = _caller_org_id(info, org_id)
        qs = visible_agent_boxes(org_pk, Permission.AGENT_READ)
        if not include_ended:
            qs = qs.filter(status__in=sorted(AgentBox.LIVE_STATUSES))
        qs = qs.order_by("-created_at")[:200]
        return [agent_box_to_type(b) for b in qs]

    @strawberry.field
    @require_permission(Permission.AGENT_READ, scope=agent_box_scope("slug"))
    @tenant_scoped()
    def agent_box(self, info: Info, slug: str) -> AgentBoxType | None:
        """One box by slug, checked at the box's own scope (#1866).

        The slug is what a client stores between sessions, so this is the
        lookup an IDE polls while a box provisions. Another org's box
        resolves to null rather than an error, so the surface leaks no
        existence.
        """
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        row = (
            visible_agent_boxes(org_pk, Permission.AGENT_READ)
            .filter(slug=slug, organization_id=org_pk)
            .first()
        )
        return agent_box_to_type(row) if row is not None else None

    @strawberry.field
    @require_permission(Permission.AGENT_ENV_SPEC_READ, any_scope=True)
    @tenant_scoped()
    def agent_environment_spec(self, info: Info, slug: str) -> AgentEnvironmentSpecType | None:
        """One AgentEnvironmentSpec by slug, among the specs the caller may
        read (#1866).

        An org-shared spec is readable wherever the caller reads specs, so
        this gates like the list and narrows to the same rows. A spec in
        another org, or one owned by a scope the caller's grants miss,
        resolves to null (not an error) so the surface leaks no existence.
        """
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        row = (
            visible_environment_specs(org_pk, Permission.AGENT_ENV_SPEC_READ)
            .filter(slug=slug, organization_id=org_pk)
            .first()
        )
        return agent_env_spec_to_type(row) if row is not None else None

    @strawberry.field
    @require_permission(Permission.SECRET_LIST, any_scope=True)
    @tenant_scoped()
    def agent_environment_spec_secret_status(self, info: Info, slug: str) -> list[AgentSecretStatusType]:
        """Per-ref presence status for a spec's ``secret_refs`` — metadata
        only (env var, uri, exists), never values.

        Narrowed to the specs the caller reaches at ``secret.list``, as the
        spec list is at ``agent_env_spec.read`` (#1866). A ref whose store
        read fails reports ``exists=false`` with a short ``error`` string
        rather than failing the whole query (mirrors the bundle-key
        swallow-and-report). A spec the caller cannot reach, in another org
        or absent, resolves to ``[]``.
        """
        from astrolift_agents.services.agent_cluster import (
            NoAgentClusterError,
            resolve_agent_cluster,
        )
        from astrolift_dispatch.agent_secrets import (
            effective_secret_refs,
            probe_ref_statuses,
            unscoped_secret_refs,
        )

        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        spec = (
            visible_environment_specs(org_pk, Permission.SECRET_LIST)
            .filter(slug=slug, organization_id=org_pk)
            .first()
        )
        if spec is None:
            return []
        try:
            cluster = resolve_agent_cluster(spec.organization)
        except NoAgentClusterError:
            cluster = None
        rows = probe_ref_statuses(
            cluster=cluster,
            refs=effective_secret_refs(spec),
            unscoped=unscoped_secret_refs(spec),
        )
        return [agent_secret_status_to_type(r) for r in rows]

    @strawberry.field
    @require_permission(Permission.SECRET_LIST, any_scope=True)
    @tenant_scoped()
    def agent_environment_spec_secret_status_page(
        self,
        info: Info,
        slug: str,
        search: str | None = None,
        filter: AgentSecretStatusFilterInput | None = None,
        sort: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> AgentSecretStatusPageType:
        """``agentEnvironmentSpecSecretStatus`` on the list contract, with an
        error for the read (#2155).

        ``search`` matches the env var and uri; ``filter``: exists, failing
        (the per-ref check reported an error) and provider; ``sort`` is a
        multi-key spec over envVar, uri, exists and provider (``envVar`` by
        default). The rows come from probing the secret store, not from a
        table, so every ref is probed and the page is cut after filtering
        and sorting; a spec carries a handful of refs.

        ``error`` says why the whole read could not answer: the spec is not
        one the caller reaches at ``secret.list`` (no rows, #1866), or the
        org has no agent cluster or secret store (rows report ``exists:
        false``). A spec in another org or another team reads exactly like
        one that does not exist.
        """
        from astrolift_agents.services.agent_cluster import (
            NoAgentClusterError,
            resolve_agent_cluster,
        )
        from astrolift_dispatch.agent_secrets import (
            effective_secret_refs,
            probe_ref_statuses,
            resolve_secrets_backend,
            unscoped_secret_refs,
        )

        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        spec = (
            visible_environment_specs(org_pk, Permission.SECRET_LIST)
            .filter(slug=slug, organization_id=org_pk)
            .first()
        )
        if spec is None:
            _, number, size = _slice_page([], page, page_size)
            return AgentSecretStatusPageType(
                items=[], total_count=0, page=number, page_size=size, error="environment spec not found"
            )
        error = None
        try:
            cluster = resolve_agent_cluster(spec.organization)
        except NoAgentClusterError as exc:
            cluster = None
            error = str(exc) or "the organization has no agent cluster"
        if cluster is not None:
            try:
                resolve_secrets_backend(cluster)
            except Exception:  # noqa: BLE001 - never reflect provider response bodies
                error = "secret store unavailable; inspect the provider audit log"
        rows = [
            agent_secret_status_to_type(r)
            for r in probe_ref_statuses(
                cluster=cluster,
                refs=effective_secret_refs(spec),
                unscoped=unscoped_secret_refs(spec),
            )
        ]
        if search and search.strip():
            needle = search.strip().lower()
            rows = [r for r in rows if needle in r.env_var.lower() or needle in r.uri.lower()]
        values = filter_values(filter)
        if "exists" in values:
            rows = [r for r in rows if r.exists is values["exists"]]
        if "failing" in values:
            rows = [r for r in rows if bool(r.error) is values["failing"]]
        if "provider" in values:
            wanted = {p.lower() for p in values["provider"]}
            rows = [r for r in rows if (r.provider or "").lower() in wanted]
        rows = _sorted_rows(
            rows, sort, _SECRET_STATUS_SORTS, default="envVar", list_name="agent secret status"
        )
        items, number, size = _slice_page(rows, page, page_size)
        return AgentSecretStatusPageType(
            items=items, total_count=len(rows), page=number, page_size=size, error=error
        )

    @strawberry.field
    @require_permission(Permission.SECRET_LIST, scope=agent_org_scope)
    @tenant_scoped()
    def agent_secret_bundles(self, info: Info, env_spec_slug: str) -> list[AgentSecretBundleType]:
        """Reusable bundles visible to this agent's organization.

        Org-level: the bundles are the org's shared secret packets, and
        wiring one to a spec is an org-level act (#1866).
        """
        from astrolift_agents.services.agent_cluster import resolve_agent_cluster
        from astrolift_agents.services.project_membership import agent_spec_belongs_to_project
        from astrolift_dispatch.agent_secrets import (
            resolve_secrets_backend,
            secret_backend_capabilities,
        )
        from astrolift_services.models import SecretBundle

        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        spec = (
            visible_environment_specs(org_pk, Permission.SECRET_LIST)
            .filter(slug=env_spec_slug, organization_id=org_pk)
            .first()
        )
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
    @require_permission(Permission.SECRET_READ, Permission.SECRET_LIST, scope=agent_org_scope)
    @tenant_scoped()
    def agent_environment_spec_secret_bundle_attachments(
        self, info: Info, slug: str
    ) -> list[AgentSecretBundleAttachmentType]:
        """Ordered reusable-secret bundles attached to one agent spec.

        Org-level, like the bundle picker: attachments are the org's secret
        wiring (#1866).
        """
        tenant = get_current_tenant()
        org_pk = tenant.organization_id if tenant else None
        spec = (
            visible_environment_specs(org_pk, Permission.SECRET_LIST)
            .filter(slug=slug, organization_id=org_pk)
            .first()
        )
        if spec is None:
            return []
        refs = (
            AgentSecretBundleRef.objects.select_related("environment_spec", "secret_bundle")
            .filter(
                environment_spec=spec,
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
    @require_permission(Permission.AGENT_READ, any_scope=True)
    @tenant_scoped()
    def agent_workloads(
        self,
        info: Info,
        org_id: strawberry.ID,
        project_slug: str | None = None,
        dispatchable: bool = False,
    ) -> list[AgentListItemType]:
        """The org's ``kind: agent`` workloads as list rows, newest first.

        ``project_slug`` narrows to one project's agents (the
        project-scoped Agents list); omitting it returns every agent in
        the org's fleet (the fleet/org-wide variant). Each row carries
        the run-spec fields (PR-1) plus a compact last-run summary +
        running count rolled up in bulk so the list stays a single
        round-trip with no per-agent query.

        ``dispatchable=True`` (#2071) narrows the list to exactly the
        agents ``runAstroliftAgent`` would accept from this caller:
        ``agent.dispatch`` rather than ``agent.read``, the same bearer
        token team/share ceiling every org-scoped agent read applies, and
        Task run family only (nothing ever dispatches a Service agent). An
        agent the caller may read but not dispatch -- or may dispatch only
        because of an ``app.read``-shaped grant that does not extend to
        ``agent.dispatch`` -- is absent from this narrowed list even
        though the default (unfiltered) list still shows it, so a client
        rendering "agents you may launch" never has to learn the gap from
        a refused ``runAstroliftAgent`` call.

        Org-scoped: ``org_id`` must match the caller's active tenant
        (superusers excepted, via ``_caller_org_id``); the workload
        queryset is filtered to that org through the app's organization.
        """
        return _agent_list_rows(info, org_id, project_slug, dispatchable=dispatchable)

    @strawberry.field
    @require_permission(Permission.AGENT_READ, any_scope=True)
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
    @require_permission(Permission.AGENT_READ, any_scope=True)
    @tenant_scoped()
    def agent_fleet_page(
        self,
        info: Info,
        org_id: strawberry.ID,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
        filter: AgentFleetFilterInput | None = None,
        sort: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> AgentListItemPageType:
        """The organization-wide agent fleet, on the list contract (#2155).

        ``filter`` takes the declared filters (project, status, model,
        runtime, cluster, paused, owner), ``search`` matches the name, slug,
        app, project and source repo, and ``sort`` is a multi-key spec over
        ``name``, ``slug``, ``status``, ``lastRun``, ``project`` and
        ``created`` (``-lastRun,name``). Any of ``sort``, ``page`` or
        ``pageSize`` selects a numbered page with an exact ``totalCount``;
        otherwise the cursor walk (``limit`` / ``after``, newest first) runs
        as before, with ``filter`` applied first. Search used to match the
        slug and name only; it now matches the wider set on both paths.

        Org-scoped through ``_caller_org_id`` and the ``agent.read``
        visibility set, so a foreign ``orgId`` raises and a foreign agent
        never reaches the filter.
        """
        org_pk = _caller_org_id(info, org_id)
        viewer = _viewer_id()
        qs = _annotate_fleet(_agent_workload_qs(org_pk), org_pk)
        if search and search.strip():
            qs = qs.filter(_fleet_search_q(search.strip()))
        values = filter_values(filter)
        qs = qs.filter(filter_q(values, _FLEET_FILTERS))
        if "owner" in values:
            qs = qs.filter(_fleet_owner_q(values["owner"], viewer))

        if page is None and page_size is None and sort is None:
            walk = keyset_page(qs, cursor=after, limit=limit, cursor_scope=f"agent-fleet:{org_pk}")
            return AgentListItemPageType(
                items=_agent_list_rows_for_workloads(walk.rows),
                next_cursor=walk.next_cursor,
                total_count=walk.total_count,
            )
        order_by = resolve_list_sort(sort, _FLEET_SORTS, default="name")
        result = numbered_page(qs, order_by=order_by, page=page, page_size=page_size)
        return AgentListItemPageType(
            items=_agent_list_rows_for_workloads(result.rows),
            next_cursor=None,
            total_count=result.total_count,
            page=result.page,
            page_size=result.page_size,
        )

    @strawberry.field
    @require_permission(Permission.AGENT_READ, any_scope=True)
    @tenant_scoped()
    def agent_tasks_page(
        self,
        info: Info,
        org_id: strawberry.ID,
        status: str | None = None,
        workload_id: strawberry.ID | None = None,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
        filter: AgentTasksFilterInput | None = None,
        sort: str | None = None,
    ) -> AgentTaskPageType:
        """Cursor-paginated task history for the organization.

        #2155: ``filter`` narrows by status (any of), trigger kind, agent,
        project and initiator (``startedBy`` user ids or "me", and
        ``startedByMe``), and ``sort`` orders by ``created`` or ``updated``
        in either direction (``-created`` by default). ``status`` (one
        value) still works and combines with the filter.
        """
        org_pk = _caller_org_id(info, org_id)
        sort_field, descending, cursor_scope = _task_sort(sort, org_pk)
        qs = visible_agent_tasks(org_pk, Permission.AGENT_READ)
        if status:
            qs = qs.filter(status=status)
        qs = _apply_task_filter(qs, filter_values(filter), _viewer_id())
        if workload_id:
            workload_guid = _valid_guid(workload_id)
            if workload_guid is None:
                return AgentTaskPageType(items=[], total_count=0)
            from astrolift_registry.models import Workload

            wl = (
                Workload.objects.filter(
                    guid=workload_guid,
                    registered_app__organization_id=org_pk,
                    deleted_at__isnull=True,
                )
                .values_list("pk", flat=True)
                .first()
            )
            if wl is None:
                return AgentTaskPageType(items=[], total_count=0)
            qs = qs.filter(agent_definition_id=wl)
        if search:
            qs = qs.filter(
                search_q(
                    search, "status", "agent_definition__slug", "agent_definition__name", "project__slug"
                )
            )
        qs = qs.select_related(
            "organization", "project", "agent_definition", "dispatcher", "dispatcher__tenant_cluster"
        )
        page = keyset_page(
            qs,
            cursor=after,
            limit=limit,
            sort_field=sort_field,
            descending=descending,
            cursor_scope=cursor_scope,
        )
        return AgentTaskPageType(
            items=agent_tasks_to_types(page.rows),
            next_cursor=page.next_cursor,
            total_count=page.total_count,
        )

    @strawberry.field
    @require_permission(Permission.AGENT_READ, scope=agent_workload_app_scope("slug"))
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
        across tenants. Slugs are unique per app; a slug matching more
        than one authorized agent resolves to ``null``.

        No N+1: the workload is loaded with ``select_related('brief',
        'registered_app')`` and ``prefetch_related(
        'agent_skill_refs__skill__tool_defs', 'containers')`` so the
        Brief, image, every skill, and every tool come back in a bounded
        number of queries regardless of how many skills/tools the agent
        carries.
        """
        org_pk = _caller_org_id(info, org_id)
        matches = list(
            _agent_workload_qs(org_pk)
            .filter(slug=slug)
            .select_related("brief", "registered_app")
            .prefetch_related("agent_skill_refs__skill__tool_defs", "containers")[:2]
        )
        return agent_detail_to_type(matches[0]) if len(matches) == 1 else None

    @strawberry.field(
        deprecation_reason=(
            "Unbounded: returns every trigger bound to the agent in one response. "
            "Use agentTriggersPage instead."
        )
    )
    @require_permission(Permission.AGENT_READ, scope=agent_workload_app_scope("agent_slug"))
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
    @require_permission(Permission.AGENT_READ, scope=agent_workload_app_scope("agent_slug"))
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
    @require_permission(Permission.AGENT_READ, scope=agent_org_scope)
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

        Read-only and org-level: the scan uses the org's own source
        connection and reports ``already_registered`` across every app in
        the org, so it checks at the explicit org scope rather than a
        selected team (#1866). ``org_id`` must match the caller's active
        tenant (superusers excepted, via ``_caller_org_id``) — a caller can
        neither scan with another tenant's credentials nor see another
        tenant's registered agents.
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
    @require_permission(Permission.AGENT_READ, any_scope=True)
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
            workload_guid = _valid_guid(workload_id)
            if workload_guid is None:
                return []
            qs = qs.filter(guid=workload_guid)
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
    @require_permission(Permission.AGENT_READ, any_scope=True)
    @tenant_scoped()
    def agent_upcoming_runs(
        self,
        info: Info,
        org_id: strawberry.ID,
        search: str | None = None,
        project: list[str] | None = None,
        agent: list[str] | None = None,
        per_agent: int = 1,
        within_hours: int | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> AgentUpcomingRunPageType:
        """Upcoming scheduled firings across the fleet, soonest first (#2155).

        For Runs › Scheduled: every unpaused schedule-mode agent the caller
        can read, with its next ``perAgent`` firings (1 to 10) computed from
        its cron expression by the platform evaluator ``agentLiveStatus``
        uses, optionally only those inside ``withinHours``. An expression
        the cron parser rejects contributes nothing.

        The times exist only in Python, so the whole filtered set is computed
        once, sorted by time (then agent slug), and cut into a numbered page;
        ``totalCount`` counts firings, not agents. ``search`` matches the
        agent name and slug; ``project`` and ``agent`` take slugs.

        Org-scoped like ``agentFleet``: ``orgId`` must be the caller's org
        and the agents are its ``agent.read`` visibility set.
        """
        from datetime import timedelta

        from django.utils import timezone

        from astrolift_registry.cron import CronValidationError, validate_cron_expression
        from astrolift_registry.models import Workload

        org_pk = _caller_org_id(info, org_id)
        qs = (
            _agent_workload_qs(org_pk)
            .filter(run_mode=Workload.RunMode.SCHEDULE, run_paused=False)
            .exclude(run_cron_expression="")
        )
        if search and search.strip():
            qs = qs.filter(search_q(search.strip(), "name", "slug"))
        if project:
            qs = qs.filter(_iexact_any("registered_app__project__slug", project))
        if agent:
            qs = qs.filter(_iexact_any("slug", agent))
        count = max(1, min(int(per_agent), _UPCOMING_PER_AGENT_MAX))
        now = timezone.now()
        until = now + timedelta(hours=within_hours) if within_hours is not None and within_hours > 0 else None
        firings: list[AgentUpcomingRunType] = []
        for w in qs:
            try:
                expression = validate_cron_expression(w.run_cron_expression)
            except CronValidationError:
                continue
            app = w.registered_app
            for at in _upcoming_firings(expression, after=now, count=count, until=until):
                firings.append(
                    AgentUpcomingRunType(
                        agent_id=GUID(str(w.guid)),
                        agent_slug=w.slug,
                        agent_name=w.name,
                        app_slug=app.slug,
                        project_slug=app.project.slug if app.project_id else "",
                        cron_expression=w.run_cron_expression,
                        scheduled_at=at,
                    )
                )
        firings.sort(key=lambda f: (f.scheduled_at, f.agent_slug, str(f.agent_id)))
        items, number, size = _slice_page(firings, page, page_size)
        return AgentUpcomingRunPageType(items=items, total_count=len(firings), page=number, page_size=size)

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
        # The whole fleet is the platform operator's to see; Django staff is
        # not the operator (#1978). The api_key_hash is never surfaced (the
        # type omits it). See EXEMPT entry in test_tenancy_guardrail.py.
        user = getattr(getattr(info.context, "request", None), "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            raise GraphQLError("authentication required")
        require_platform_operator(user)
        qs = DispatcherInstance.objects.filter(deleted_at__isnull=True).order_by("slug")[:200]
        return [dispatcher_to_type(d) for d in qs]
