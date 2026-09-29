"""One paged list over everything that ran (spec 44 §4.4, decision 14; #2152).

Agent tasks, workflow definition runs, deployments, scheduled job runs and
task runs, merged into one cursor list for Admin › Usage & governance ›
Runs and Agents › Runs. Each kind keeps its own table; the merge is a SQL
``UNION ALL`` of one narrow row per source (kind, guid, time), ordered and
cut there, so a page boundary is right across kinds. The page's rows are
then read back in bulk, one query per kind.

Sort key: ``at`` = ``COALESCE(started_at, created_at)``, which is never
NULL, with ``(kind, guid)`` as the tiebreak. A cursor carries all three
plus the sort it was minted under, so a sort change restarts the walk.

Each kind is read under its own permission, at any scope, with its rows
narrowed to the scopes the caller holds: ``agent.read`` for agent tasks,
``workflow.read`` for workflow runs, ``app.read`` for deployments,
``app.read_logs`` for job and task runs (as their own lists gate). A kind
the caller cannot read, or whose feature is off, is left out; a caller
who can read none gets PermissionDenied.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field

import strawberry
from django.db.models import CharField, F, Q, QuerySet, Value
from django.db.models.functions import Coalesce
from django.utils.dateparse import parse_datetime

from astrolift_graphql import (
    PageType,
    UnsupportedSort,
    clamp_limit,
    decode_cursor,
    encode_cursor,
    filter_values,
    parse_sort_spec,
    search_q,
)
from core.permissions import Permission, PermissionDenied, check_permission_any_scope
from core.run_trigger import RunTrigger, normalize, source_triggers

RUN_KINDS = ("agent", "workflow", "deployment", "job", "task")


@strawberry.input(
    name="AstroliftRunAuditFilter",
    description="The run audit's declared filters. Unset fields do not filter; list values match any.",
)
class RunAuditFilterInput:
    kind: list[str] | None = strawberry.field(
        default=None, description="agent, workflow, deployment, job, task."
    )
    outcome: list[str] | None = strawberry.field(
        default=None,
        description="running, waiting, succeeded, failed, cancelled, unknown (each kind's status, normalised).",
    )
    status: list[str] | None = strawberry.field(default=None, description="The source's own status word.")
    agent: list[str] | None = strawberry.field(
        default=None, description="Agent workload slugs. Only agent runs carry one; other kinds drop out."
    )
    workflow: list[str] | None = strawberry.field(
        default=None,
        description="Workflow definition slugs. Only workflow runs carry one; other kinds drop out.",
    )
    project: list[str] | None = strawberry.field(
        default=None, description="Project slugs: the run's own project, else its app's."
    )
    app: list[str] | None = strawberry.field(default=None, description="App slugs.")
    trigger: list[str] | None = strawberry.field(
        default=None, description="manual, api, schedule, webhook, parent, unknown."
    )
    started_by: list[str] | None = strawberry.field(
        default=None, description='The initiator: user pks as strings, or "me".'
    )
    since: dt.datetime | None = strawberry.field(default=None, description="at >= since.")
    until: dt.datetime | None = strawberry.field(default=None, description="at <= until.")


@strawberry.type(name="AstroliftRunAuditItem", description="One run of any kind, as the run audit lists it.")
class RunAuditItemType:
    kind: str = strawberry.field(description="agent, workflow, deployment, job, task.")
    id: str = strawberry.field(description="The run's guid, as its own detail query takes it.")
    subject: str = strawberry.field(
        description="What ran: the agent, workflow, app and environment, job, task."
    )
    scope: str = strawberry.field(description="The project or app it belongs to.")
    agent_slug: str
    workflow_slug: str = strawberry.field(
        description="The definition slug; a workflow run's page is keyed on it."
    )
    project_slug: str
    app_slug: str
    environment_name: str
    trigger: str = strawberry.field(description="manual, api, schedule, webhook, parent, unknown.")
    source_trigger: str = strawberry.field(
        description="The source's own trigger word (a deployment's push, ci, rollback, ...)."
    )
    started_by_kind: str = strawberry.field(
        description="user, token, schedule, ci, webhook, parent_run or system."
    )
    started_by_id: str | None = strawberry.field(
        description="The initiator's user pk, when a person started it."
    )
    started_by_display: str = strawberry.field(
        description="The initiator's name, else a deployment's commit author, else empty."
    )
    started_by_me: bool
    at: dt.datetime = strawberry.field(description="When it started, or was created if it has not yet.")
    started_at: dt.datetime | None
    ended_at: dt.datetime | None
    duration_seconds: int | None
    status: str = strawberry.field(description="The source's own status word.")
    outcome: str = strawberry.field(description="running, waiting, succeeded, failed, cancelled, unknown.")


# ---------------------------------------------------------------------------
# Outcomes (a port of ``outcomeOf`` in the frontend's combined-runs.ts)
# ---------------------------------------------------------------------------

_GENERIC_OUTCOMES = {
    "running": "running",
    "in_progress": "running",
    "provisioning": "running",
    "starting": "running",
    "draft": "waiting",
    "queued": "waiting",
    "pending": "waiting",
    "waiting": "waiting",
    "paused": "waiting",
    "awaiting_approval": "waiting",
    "completed": "succeeded",
    "succeeded": "succeeded",
    "success": "succeeded",
    "failed": "failed",
    "error": "failed",
    "timed_out": "failed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "aborted": "cancelled",
    "terminated": "cancelled",
    "superseded": "cancelled",
}

_DEPLOYMENT_OUTCOMES = {
    "running": "succeeded",
    "superseded": "succeeded",
    "deploying": "running",
    "redeploying": "running",
    "pending": "waiting",
    "pending_approval": "waiting",
    "failed": "failed",
    "rolled_back": "failed",
}


def outcome_of(kind: str, status: str) -> str:
    """A source status as an outcome. A ``running`` deployment is live, so it succeeded."""
    s = (status or "").lower()
    if kind == "deployment" and s in _DEPLOYMENT_OUTCOMES:
        return _DEPLOYMENT_OUTCOMES[s]
    return _GENERIC_OUTCOMES.get(s, "unknown")


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Source:
    kind: str
    model: Callable[[], type]
    statuses: Callable[[], list[str]]
    user_field: str
    trigger_field: str
    agent: str | None
    workflow: str | None
    project: tuple[str, ...]
    app: str
    search: tuple[str, ...]
    related: tuple[str, ...] = field(default=())


def _agent_task():
    from astrolift_agents.models import AgentTask

    return AgentTask


def _workflow_run():
    from astrolift_operations.models import WorkflowRun

    return WorkflowRun


def _deployment():
    from astrolift_lifecycle.models import Deployment

    return Deployment


def _job_run():
    from astrolift_lifecycle.models import ScheduledJobRun

    return ScheduledJobRun


def _task_run():
    from astrolift_lifecycle.models import TaskRun

    return TaskRun


_SOURCES: dict[str, _Source] = {
    "agent": _Source(
        kind="agent",
        model=_agent_task,
        statuses=lambda: list(_agent_task().Status.values),
        user_field="triggered_by_user_id",
        trigger_field="trigger_kind",
        agent="agent_definition__slug",
        workflow=None,
        project=("project__slug", "agent_definition__registered_app__project__slug"),
        app="agent_definition__registered_app__slug",
        search=("status", "agent_definition__slug", "agent_definition__name", "project__slug"),
        related=("project", "agent_definition__registered_app__project", "triggered_by_user"),
    ),
    "workflow": _Source(
        kind="workflow",
        model=_workflow_run,
        statuses=lambda: list(_workflow_run().Status.values),
        user_field="trigger_actor_user_id",
        trigger_field="trigger_kind",
        agent=None,
        workflow="workflow_definition__slug",
        project=("workflow_definition__project__slug",),
        app="registered_app__slug",
        search=("status", "workflow_definition__slug", "workflow_definition__name"),
        related=("workflow_definition__project", "registered_app", "trigger_actor_user"),
    ),
    "deployment": _Source(
        kind="deployment",
        model=_deployment,
        statuses=lambda: list(_deployment().Status.values),
        user_field="triggered_by_user_id",
        trigger_field="trigger_kind",
        agent=None,
        workflow=None,
        project=("registered_app__project__slug",),
        app="registered_app__slug",
        search=(
            "status",
            "registered_app__slug",
            "registered_app__name",
            "app_environment__name",
            "branch",
            "commit_author",
        ),
        related=("registered_app__project", "app_environment", "triggered_by_user"),
    ),
    "job": _Source(
        kind="job",
        model=_job_run,
        statuses=lambda: list(_job_run().Status.values),
        user_field="triggered_by_id",
        trigger_field="trigger_kind",
        agent=None,
        workflow=None,
        project=("workload__registered_app__project__slug",),
        app="workload__registered_app__slug",
        search=("status", "workload__slug", "workload__registered_app__slug", "app_environment__name"),
        related=("workload__registered_app__project", "app_environment", "triggered_by"),
    ),
    "task": _Source(
        kind="task",
        model=_task_run,
        statuses=lambda: list(_task_run().Status.values),
        user_field="triggered_by_user_id",
        trigger_field="trigger_kind",
        agent=None,
        workflow=None,
        project=("workload__registered_app__project__slug",),
        app="workload__registered_app__slug",
        search=("status", "workload__slug", "workload__registered_app__slug"),
        related=("workload__registered_app__project", "app_environment", "triggered_by_user"),
    ),
}


def _allowed(permission: Permission) -> bool:
    try:
        check_permission_any_scope(permission)
    except PermissionDenied:
        return False
    return True


def _visible_app_ids(org_id: int, permission: Permission) -> QuerySet:
    from astrolift_identity.scope_visibility import visible_apps
    from astrolift_registry.models import RegisteredApp

    apps = RegisteredApp.objects.filter(organization_id=org_id, deleted_at__isnull=True)
    return visible_apps(apps, permission).values("pk")


def _scoped(kind: str, org_id: int) -> QuerySet | None:
    """The caller's readable rows of one kind, or ``None`` when it cannot read the kind."""
    from config.features import Feature, is_enabled

    if kind == "agent":
        if not is_enabled(Feature.AGENTS) or not _allowed(Permission.AGENT_READ):
            return None
        from astrolift_agents.visibility import agent_tasks

        return agent_tasks(org_id, Permission.AGENT_READ)
    if kind == "workflow":
        if not is_enabled(Feature.WORKFLOWS) or not _allowed(Permission.WORKFLOW_READ):
            return None
        from workflows.scopes import visible_runs

        qs = _workflow_run().objects.filter(
            organization_id=org_id,
            workflow_kind="WorkflowDefinitionRunWorkflow",
            workflow_definition_id__isnull=False,
            workflow_definition__deleted_at__isnull=True,
        )
        return visible_runs(qs, org_id, Permission.WORKFLOW_READ)
    if kind == "deployment":
        if not _allowed(Permission.APP_READ):
            return None
        return _deployment().objects.filter(
            registered_app__organization_id=org_id,
            registered_app__deleted_at__isnull=True,
            registered_app_id__in=_visible_app_ids(org_id, Permission.APP_READ),
        )
    if not _allowed(Permission.APP_READ_LOGS):
        return None
    model = _job_run() if kind == "job" else _task_run()
    return model.objects.filter(
        workload__registered_app__organization_id=org_id,
        workload__registered_app__deleted_at__isnull=True,
        workload__registered_app_id__in=_visible_app_ids(org_id, Permission.APP_READ_LOGS),
    )


def _user_ids(values: list[str], viewer_id: int | None) -> list[int]:
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


def _filtered(source: _Source, qs: QuerySet, f: dict, search: str | None, viewer_id: int | None):
    """``qs`` narrowed by the filter and search, or ``None`` when the kind is ruled out."""
    if "agent" in f:
        if source.agent is None:
            return None
        qs = qs.filter(**{f"{source.agent}__in": f["agent"]})
    if "workflow" in f:
        if source.workflow is None:
            return None
        qs = qs.filter(**{f"{source.workflow}__in": f["workflow"]})
    if "project" in f:
        # The run's own project; a fallback path (an agent task's app's
        # project) applies only when the run records none.
        own, *fallbacks = source.project
        project = Q(**{f"{own}__in": f["project"]})
        for path in fallbacks:
            project |= Q(**{f"{own.removesuffix('__slug')}__isnull": True, f"{path}__in": f["project"]})
        qs = qs.filter(project)
    if "app" in f:
        qs = qs.filter(**{f"{source.app}__in": f["app"]})
    if "status" in f:
        qs = qs.filter(status__in=f["status"])
    if "outcome" in f:
        wanted = set(f["outcome"])
        qs = qs.filter(status__in=[s for s in source.statuses() if outcome_of(source.kind, s) in wanted])
    if "trigger" in f:
        words = source_triggers(source.kind, f["trigger"])
        qs = qs.filter(**{f"{source.trigger_field}__in": f["trigger"] if words is None else words})
    if "started_by" in f:
        qs = qs.filter(**{f"{source.user_field}__in": _user_ids(f["started_by"], viewer_id)})
    if search:
        user_path = source.user_field.removesuffix("_id")
        qs = qs.filter(search_q(search, *source.search, f"{user_path}__username", prefix=("guid",)))
    return qs


def _sort(sort: str | None) -> tuple[bool, str]:
    pairs = parse_sort_spec(sort) or [("at", True)]
    if len(pairs) > 1 or pairs[0][0] != "at":
        raise UnsupportedSort(f"sort {sort!r} is not available on the run audit; supported: at")
    descending = pairs[0][1]
    return descending, "runs:-at" if descending else "runs:at"


def _seek(kind: str, *, at: dt.datetime, cursor_kind: str, guid: str, descending: bool) -> Q:
    """Rows of ``kind`` strictly after the cursor in ``(at, kind, guid)`` order."""
    op = "lt" if descending else "gt"
    if kind == cursor_kind:
        return Q(**{f"ra_at__{op}": at}) | Q(ra_at=at, **{f"guid__{op}": guid})
    after_on_tie = kind < cursor_kind if descending else kind > cursor_kind
    return Q(**{f"ra_at__{op}e" if after_on_tie else f"ra_at__{op}": at})


def _starter(kind: str, trigger: str, source_trigger: str, user_id: int | None) -> str:
    if trigger == RunTrigger.API:
        return "ci" if kind == "deployment" and source_trigger == "ci" else "token"
    if trigger == RunTrigger.SCHEDULE:
        return "schedule"
    if trigger == RunTrigger.WEBHOOK:
        return "webhook"
    if trigger == RunTrigger.PARENT:
        return "parent_run"
    return "user" if user_id is not None else "system"


def _display_name(user) -> str:
    if user is None:
        return ""
    return (user.get_full_name() or "").strip() or user.username


def _duration(row) -> int | None:
    stored = getattr(row, "duration_seconds", None)
    if stored is not None:
        return stored
    ended = getattr(row, "ended_at", None)
    if row.started_at and ended and ended >= row.started_at:
        return int((ended - row.started_at).total_seconds())
    return None


def _item(kind: str, row, *, viewer_id: int | None) -> RunAuditItemType:
    agent_slug = workflow_slug = project_slug = app_slug = env_name = ""
    user = None
    if kind == "agent":
        definition = row.agent_definition
        agent_slug = definition.slug if definition is not None else ""
        project = row.project or (definition.registered_app.project if definition is not None else None)
        project_slug = project.slug if project is not None else ""
        app_slug = definition.registered_app.slug if definition is not None else ""
        subject = agent_slug or (definition.name if definition is not None else "")
        scope = project_slug
        user = row.triggered_by_user
    elif kind == "workflow":
        definition = row.workflow_definition
        workflow_slug = definition.slug or ""
        project_slug = definition.project.slug if definition.project_id is not None else ""
        app_slug = row.registered_app.slug if row.registered_app_id is not None else ""
        subject = definition.name or workflow_slug
        scope = project_slug
        user = row.trigger_actor_user
    elif kind == "deployment":
        app = row.registered_app
        app_slug = app.slug
        project_slug = app.project.slug if app.project_id is not None else ""
        env_name = row.app_environment.name
        subject = f"{app_slug} · {env_name}"
        scope = app_slug
        user = row.triggered_by_user
    else:
        app = row.workload.registered_app
        app_slug = app.slug
        project_slug = app.project.slug if app.project_id is not None else ""
        env_name = row.app_environment.name if row.app_environment_id is not None else ""
        subject = row.workload.slug
        scope = f"{app_slug} · {env_name}" if kind == "job" and env_name else app_slug
        user = row.triggered_by if kind == "job" else row.triggered_by_user

    source_trigger = row.trigger_kind or ""
    trigger = normalize(kind, source_trigger)
    user_id = user.pk if user is not None else None
    display = _display_name(user)
    if not display and kind == "deployment":
        display = row.commit_author or ""
    return RunAuditItemType(
        kind=kind,
        id=str(row.guid),
        subject=subject,
        scope=scope,
        agent_slug=agent_slug,
        workflow_slug=workflow_slug,
        project_slug=project_slug,
        app_slug=app_slug,
        environment_name=env_name,
        trigger=trigger,
        source_trigger=source_trigger,
        started_by_kind=_starter(kind, trigger, source_trigger, user_id),
        started_by_id=str(user_id) if user_id is not None else None,
        started_by_display=display,
        started_by_me=user_id is not None and user_id == viewer_id,
        at=row.started_at or row.created_at,
        started_at=row.started_at,
        ended_at=row.ended_at,
        duration_seconds=_duration(row),
        status=row.status,
        outcome=outcome_of(kind, row.status),
    )


def run_audit_page(
    org_id: int | None,
    *,
    filter: RunAuditFilterInput | None = None,
    search: str | None = None,
    sort: str | None = None,
    first: int | None = None,
    after: str | None = None,
    viewer_id: int | None = None,
) -> PageType[RunAuditItemType]:
    """One page of the run audit. See the module docstring."""
    descending, cursor_scope = _sort(sort)
    if org_id is None:
        return PageType(items=[], next_cursor=None, total_count=0)

    readable = {kind: qs for kind in RUN_KINDS if (qs := _scoped(kind, org_id)) is not None}
    if not readable:
        raise PermissionDenied(Permission.AGENT_READ, None, "no read permission for any kind of run")

    f = filter_values(filter)
    kinds = [k for k in RUN_KINDS if k in readable and ("kind" not in f or k in f["kind"])]
    term = search.strip() if search and search.strip() else None

    parts: dict[str, QuerySet] = {}
    for kind in kinds:
        qs = _filtered(_SOURCES[kind], readable[kind], f, term, viewer_id)
        if qs is None:
            continue
        qs = qs.annotate(
            ra_kind=Value(kind, output_field=CharField()),
            ra_guid=F("guid"),
            ra_at=Coalesce("started_at", "created_at"),
        )
        if "since" in f:
            qs = qs.filter(ra_at__gte=f["since"])
        if "until" in f:
            qs = qs.filter(ra_at__lte=f["until"])
        parts[kind] = qs.order_by()

    limit = clamp_limit(first, default=25)
    if not parts:
        return PageType(items=[], next_cursor=None, total_count=0)

    total = sum(qs.count() for qs in parts.values())

    decoded = decode_cursor(after, arity=4) if after else None
    if decoded is not None and decoded[0] == cursor_scope:
        cursor_at = parse_datetime(decoded[1])
        if cursor_at is not None:
            parts = {
                kind: qs.filter(
                    _seek(kind, at=cursor_at, cursor_kind=decoded[2], guid=decoded[3], descending=descending)
                )
                for kind, qs in parts.items()
            }

    selects = [qs.values_list("ra_kind", "ra_guid", "ra_at") for qs in parts.values()]
    union = selects[0].union(*selects[1:], all=True) if len(selects) > 1 else selects[0]
    direction = "-" if descending else ""
    keys = list(
        union.order_by(f"{direction}ra_at", f"{direction}ra_kind", f"{direction}ra_guid")[: limit + 1]
    )
    has_more = len(keys) > limit
    keys = keys[:limit]

    rows: dict[tuple[str, str], object] = {}
    for kind in {k for k, _g, _a in keys}:
        source = _SOURCES[kind]
        guids = [g for k, g, _a in keys if k == kind]
        # tenancy: parts[kind] is the kind's org-scoped queryset from
        # _filtered, so the fetch-by-guid carries the same org clause (#1183).
        for row in parts[kind].filter(guid__in=guids).select_related(*source.related):
            rows[(kind, str(row.guid))] = row
    items = [_item(k, rows[(k, str(g))], viewer_id=viewer_id) for k, g, _a in keys if (k, str(g)) in rows]

    next_cursor = None
    if has_more and keys:
        last_kind, last_guid, last_at = keys[-1]
        next_cursor = encode_cursor(cursor_scope, last_at, last_kind, last_guid)
    return PageType(items=items, next_cursor=next_cursor, total_count=total)
