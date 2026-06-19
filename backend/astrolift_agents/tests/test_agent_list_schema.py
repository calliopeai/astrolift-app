"""Schema tests for the Agents list + live-status surface (spec 33 PR-2).

Covers the read side the Agents list page and the per-agent detail
header consume:

* ``agentWorkloads(projectSlug:)`` — project-scoped agent list rows.
* ``agentFleet`` — org-wide agent list (no project filter).
* ``agentLiveStatus`` — per-agent running/idle/paused + next-scheduled.
* ``agentTasks(workloadId:)`` — the per-agent task-history filter.

Every case binds a real tenant context + a controllable permission
resolver and invokes the resolver directly (the agents-test
convention). The database is real. Cross-tenant isolation is asserted
explicitly because the run-spec slugs (project/app) are only unique
*within* an org.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from django.utils import timezone

from astrolift_agents.models import AgentTask
from astrolift_agents.schema.queries import AgentsQuery, _next_cron_fire
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AgentRun
from astrolift_registry.models import RegisteredApp, Workload
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def info():
    def _make(request=None):
        return SimpleNamespace(context=SimpleNamespace(user=None, request=request))

    return _make


@pytest.fixture
def with_tenant_org():
    def _enter(org, *, actor_user_id=None):
        return _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=actor_user_id))

    return _enter


def _org(slug):
    return Organization.objects.create(name=slug.replace("-", " ").title(), slug=slug)


def _project(org, slug):
    team = Team.objects.create(organization=org, name=f"{slug} team", slug=f"{slug}-team")
    return Project.objects.create(organization=org, team=team, name=slug.title(), slug=slug), team


def _app(org, project, team, slug, **kwargs):
    return RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name=slug.title(),
        slug=slug,
        provisioning_status="ready",
        **kwargs,
    )


def _agent_workload(app, slug, **kwargs):
    return Workload.objects.create(
        registered_app=app,
        name=slug.title(),
        slug=slug,
        kind=Workload.Kind.AGENT,
        **kwargs,
    )


def _run(workload, *, status=AgentRun.Status.SUCCEEDED, created_at=None, started_at=None):
    run = AgentRun.objects.create(workload=workload, status=status)
    # created_at is auto_now_add; override via queryset update so the
    # ordering / "last run" assertions are deterministic.
    if created_at is not None or started_at is not None:
        AgentRun.objects.filter(pk=run.pk).update(
            created_at=created_at or run.created_at,
            started_at=started_at,
        )
        run.refresh_from_db()
    return run


# ---------------------------------------------------------------------------
# agentWorkloads / agentFleet
# ---------------------------------------------------------------------------


def test_agent_workloads_lists_only_agents_in_project(permission_resolver, info, with_tenant_org):
    org = _org("acme")
    project, team = _project(org, "demo")
    app = _app(org, project, team, "hello")
    permission_resolver.grant(Permission.AGENT_READ)

    agent = _agent_workload(app, "triage-bot")
    # A non-agent workload on the same app must NOT appear.
    Workload.objects.create(registered_app=app, name="Web", slug="web", kind=Workload.Kind.DEPLOYMENT)

    with with_tenant_org(org):
        rows = AgentsQuery().agent_workloads(info(), org_id=str(org.guid), project_slug="demo")

    slugs = {r.slug for r in rows}
    assert slugs == {"triage-bot"}
    row = rows[0]
    assert str(row.id) == str(agent.guid)
    assert row.app_slug == "hello"
    assert row.project_slug == "demo"
    assert row.run_family == "task"
    assert row.run_mode == "once"


def test_agent_workloads_carries_source_and_run_spec(permission_resolver, info, with_tenant_org):
    org = _org("acme")
    project, team = _project(org, "demo")
    app = _app(org, project, team, "hello", source_repo="acme/agents", source_url="https://github.com/acme/agents")
    permission_resolver.grant(Permission.AGENT_READ)
    _agent_workload(
        app,
        "cron-bot",
        run_mode=Workload.RunMode.SCHEDULE,
        run_cron_expression="0 9 * * *",
        run_paused=True,
    )

    with with_tenant_org(org):
        rows = AgentsQuery().agent_workloads(info(), org_id=str(org.guid))

    row = rows[0]
    assert row.source_repo == "acme/agents"
    assert row.source_url == "https://github.com/acme/agents"
    assert row.run_mode == "schedule"
    assert row.run_cron_expression == "0 9 * * *"
    assert row.run_paused is True


def test_agent_workloads_project_filter_excludes_other_projects(permission_resolver, info, with_tenant_org):
    org = _org("acme")
    proj_a, team_a = _project(org, "alpha")
    proj_b, team_b = _project(org, "beta")
    app_a = _app(org, proj_a, team_a, "app-a")
    app_b = _app(org, proj_b, team_b, "app-b")
    permission_resolver.grant(Permission.AGENT_READ)
    _agent_workload(app_a, "agent-a")
    _agent_workload(app_b, "agent-b")

    with with_tenant_org(org):
        rows = AgentsQuery().agent_workloads(info(), org_id=str(org.guid), project_slug="alpha")

    assert {r.slug for r in rows} == {"agent-a"}


def test_agent_fleet_spans_all_projects(permission_resolver, info, with_tenant_org):
    org = _org("acme")
    proj_a, team_a = _project(org, "alpha")
    proj_b, team_b = _project(org, "beta")
    app_a = _app(org, proj_a, team_a, "app-a")
    app_b = _app(org, proj_b, team_b, "app-b")
    permission_resolver.grant(Permission.AGENT_READ)
    _agent_workload(app_a, "agent-a")
    _agent_workload(app_b, "agent-b")

    with with_tenant_org(org):
        rows = AgentsQuery().agent_fleet(info(), org_id=str(org.guid))

    assert {r.slug for r in rows} == {"agent-a", "agent-b"}


def test_agent_workloads_last_run_summary(permission_resolver, info, with_tenant_org):
    org = _org("acme")
    project, team = _project(org, "demo")
    app = _app(org, project, team, "hello")
    permission_resolver.grant(Permission.AGENT_READ)
    agent = _agent_workload(app, "triage-bot")

    now = timezone.now()
    _run(agent, status=AgentRun.Status.SUCCEEDED, created_at=now - dt.timedelta(hours=2))
    # Newest run is the FAILED one — it should win the "last run" slot.
    _run(
        agent,
        status=AgentRun.Status.FAILED,
        created_at=now - dt.timedelta(minutes=5),
        started_at=now - dt.timedelta(minutes=5),
    )
    # Two in-flight runs.
    _run(agent, status=AgentRun.Status.RUNNING, created_at=now - dt.timedelta(minutes=1))
    _run(agent, status=AgentRun.Status.RUNNING, created_at=now)

    with with_tenant_org(org):
        rows = AgentsQuery().agent_workloads(info(), org_id=str(org.guid))

    row = rows[0]
    # Latest by created_at is one of the two RUNNING rows (created now).
    assert row.last_run_status == "running"
    assert row.running_count == 2


def test_agent_workloads_no_runs_yet(permission_resolver, info, with_tenant_org):
    org = _org("acme")
    project, team = _project(org, "demo")
    app = _app(org, project, team, "hello")
    permission_resolver.grant(Permission.AGENT_READ)
    _agent_workload(app, "fresh-bot")

    with with_tenant_org(org):
        rows = AgentsQuery().agent_workloads(info(), org_id=str(org.guid))

    row = rows[0]
    assert row.last_run_status is None
    assert row.last_run_at is None
    assert row.running_count == 0


def test_agent_workloads_cross_tenant_isolation(permission_resolver, info, with_tenant_org):
    """A caller scoped to org A must never see org B's agents, even
    though both orgs can reuse the same project / app / workload slugs."""
    org_a = _org("org-a")
    org_b = _org("org-b")
    proj_a, team_a = _project(org_a, "shared-slug")
    proj_b, team_b = _project(org_b, "shared-slug")
    app_a = _app(org_a, proj_a, team_a, "shared-app")
    app_b = _app(org_b, proj_b, team_b, "shared-app")
    permission_resolver.grant(Permission.AGENT_READ)
    _agent_workload(app_a, "agent-a")
    _agent_workload(app_b, "agent-b")

    with with_tenant_org(org_a):
        rows = AgentsQuery().agent_workloads(info(), org_id=str(org_a.guid), project_slug="shared-slug")

    assert {r.slug for r in rows} == {"agent-a"}


def test_agent_workloads_rejects_foreign_org_id(permission_resolver, info, with_tenant_org):
    from graphql import GraphQLError

    org_a = _org("org-a")
    org_b = _org("org-b")
    permission_resolver.grant(Permission.AGENT_READ)
    with with_tenant_org(org_a):
        with pytest.raises(GraphQLError):
            AgentsQuery().agent_workloads(info(), org_id=str(org_b.guid))


def test_agent_workloads_requires_agent_read(info, with_tenant_org):
    # Re-gated APP_READ → AGENT_READ in the entity-module re-shell
    # (spec 36 §0.4): the agent workload/run readers now demand the
    # standalone agent.read perm, not app.read.
    org = _org("acme")
    with with_tenant_org(org):
        with pytest.raises(PermissionDenied) as exc_info:
            AgentsQuery().agent_workloads(info(), org_id=str(org.guid))
    assert exc_info.value.permission.value == "agent.read"


# ---------------------------------------------------------------------------
# agentLiveStatus
# ---------------------------------------------------------------------------


def test_live_status_running_and_idle(permission_resolver, info, with_tenant_org):
    org = _org("acme")
    project, team = _project(org, "demo")
    app = _app(org, project, team, "hello")
    permission_resolver.grant(Permission.AGENT_READ)
    busy = _agent_workload(app, "busy-bot")
    idle = _agent_workload(app, "idle-bot")
    _run(busy, status=AgentRun.Status.RUNNING)
    _run(idle, status=AgentRun.Status.SUCCEEDED)

    with with_tenant_org(org):
        rows = AgentsQuery().agent_live_status(info(), org_id=str(org.guid))

    by_slug = {r.workload_slug: r for r in rows}
    assert by_slug["busy-bot"].running_count == 1
    assert by_slug["busy-bot"].is_idle is False
    assert by_slug["idle-bot"].running_count == 0
    assert by_slug["idle-bot"].is_idle is True
    assert by_slug["idle-bot"].last_run_status == "succeeded"


def test_live_status_next_scheduled_for_schedule_mode(permission_resolver, info, with_tenant_org):
    org = _org("acme")
    project, team = _project(org, "demo")
    app = _app(org, project, team, "hello")
    permission_resolver.grant(Permission.AGENT_READ)
    _agent_workload(
        app,
        "cron-bot",
        run_mode=Workload.RunMode.SCHEDULE,
        run_cron_expression="*/5 * * * *",  # every 5 minutes
    )

    with with_tenant_org(org):
        rows = AgentsQuery().agent_live_status(info(), org_id=str(org.guid))

    status = rows[0]
    assert status.run_mode == "schedule"
    assert status.next_scheduled_at is not None
    # Next firing must be in the future and land on a 5-minute boundary.
    assert status.next_scheduled_at > timezone.now()
    assert status.next_scheduled_at.minute % 5 == 0


def test_live_status_no_next_scheduled_when_paused(permission_resolver, info, with_tenant_org):
    org = _org("acme")
    project, team = _project(org, "demo")
    app = _app(org, project, team, "hello")
    permission_resolver.grant(Permission.AGENT_READ)
    _agent_workload(
        app,
        "paused-cron",
        run_mode=Workload.RunMode.SCHEDULE,
        run_cron_expression="*/5 * * * *",
        run_paused=True,
    )

    with with_tenant_org(org):
        rows = AgentsQuery().agent_live_status(info(), org_id=str(org.guid))

    assert rows[0].is_paused is True
    assert rows[0].next_scheduled_at is None


def test_live_status_no_next_scheduled_for_once_mode(permission_resolver, info, with_tenant_org):
    org = _org("acme")
    project, team = _project(org, "demo")
    app = _app(org, project, team, "hello")
    permission_resolver.grant(Permission.AGENT_READ)
    # ONCE mode (the default) is on-demand — no clock-derived next time.
    _agent_workload(app, "manual-bot", run_cron_expression="*/5 * * * *")

    with with_tenant_org(org):
        rows = AgentsQuery().agent_live_status(info(), org_id=str(org.guid))

    assert rows[0].run_mode == "once"
    assert rows[0].next_scheduled_at is None


def test_live_status_workload_id_narrows_to_one_agent(permission_resolver, info, with_tenant_org):
    org = _org("acme")
    project, team = _project(org, "demo")
    app = _app(org, project, team, "hello")
    permission_resolver.grant(Permission.AGENT_READ)
    target = _agent_workload(app, "target-bot")
    _agent_workload(app, "other-bot")

    with with_tenant_org(org):
        rows = AgentsQuery().agent_live_status(
            info(), org_id=str(org.guid), workload_id=str(target.guid)
        )

    assert len(rows) == 1
    assert rows[0].workload_slug == "target-bot"


def test_live_status_foreign_workload_id_returns_empty(permission_resolver, info, with_tenant_org):
    """A workload GUID from another org must yield no status — not the
    other org's agent, and not the whole fleet."""
    org_a = _org("org-a")
    org_b = _org("org-b")
    proj_b, team_b = _project(org_b, "demo")
    app_b = _app(org_b, proj_b, team_b, "hello")
    foreign = _agent_workload(app_b, "foreign-bot")
    permission_resolver.grant(Permission.AGENT_READ)

    with with_tenant_org(org_a):
        rows = AgentsQuery().agent_live_status(
            info(), org_id=str(org_a.guid), workload_id=str(foreign.guid)
        )

    assert rows == []


# ---------------------------------------------------------------------------
# agentTasks(workloadId:) filter
# ---------------------------------------------------------------------------


def test_agent_tasks_filtered_by_workload(permission_resolver, info, with_tenant_org):
    org = _org("acme")
    project, team = _project(org, "demo")
    app = _app(org, project, team, "hello")
    permission_resolver.grant(Permission.AGENT_READ)
    agent_a = _agent_workload(app, "agent-a")
    agent_b = _agent_workload(app, "agent-b")
    task_a = AgentTask.objects.create(organization=org, agent_definition=agent_a, status="queued")
    AgentTask.objects.create(organization=org, agent_definition=agent_b, status="queued")

    with with_tenant_org(org):
        rows = AgentsQuery().agent_tasks(info(), org_id=str(org.guid), workload_id=str(agent_a.guid))

    assert len(rows) == 1
    assert str(rows[0].id) == str(task_a.guid)


def test_agent_tasks_foreign_workload_returns_empty(permission_resolver, info, with_tenant_org):
    """A workload GUID from another org must yield [] — not the caller
    org's full task list (the no-match must not become an unfiltered
    query)."""
    org_a = _org("org-a")
    org_b = _org("org-b")
    proj_b, team_b = _project(org_b, "demo")
    app_b = _app(org_b, proj_b, team_b, "hello")
    foreign = _agent_workload(app_b, "foreign-bot")
    permission_resolver.grant(Permission.AGENT_READ)
    # org-a has its own task — it must NOT be returned for a foreign filter.
    AgentTask.objects.create(organization=org_a, status="queued")

    with with_tenant_org(org_a):
        rows = AgentsQuery().agent_tasks(info(), org_id=str(org_a.guid), workload_id=str(foreign.guid))

    assert rows == []


def test_agent_tasks_workload_and_status_compose(permission_resolver, info, with_tenant_org):
    org = _org("acme")
    project, team = _project(org, "demo")
    app = _app(org, project, team, "hello")
    permission_resolver.grant(Permission.AGENT_READ)
    agent = _agent_workload(app, "agent-a")
    AgentTask.objects.create(organization=org, agent_definition=agent, status="queued")
    running = AgentTask.objects.create(organization=org, agent_definition=agent, status="running")

    with with_tenant_org(org):
        rows = AgentsQuery().agent_tasks(
            info(), org_id=str(org.guid), workload_id=str(agent.guid), status="running"
        )

    assert len(rows) == 1
    assert str(rows[0].id) == str(running.guid)


# ---------------------------------------------------------------------------
# _next_cron_fire helper
# ---------------------------------------------------------------------------


def test_next_cron_fire_hourly():
    after = dt.datetime(2026, 6, 18, 9, 17, tzinfo=dt.UTC)
    nxt = _next_cron_fire("0 * * * *", after=after)
    assert nxt == dt.datetime(2026, 6, 18, 10, 0, tzinfo=dt.UTC)


def test_next_cron_fire_strictly_after_current_minute():
    """A cron matching the current minute reports the NEXT occurrence."""
    after = dt.datetime(2026, 6, 18, 10, 0, 0, tzinfo=dt.UTC)
    nxt = _next_cron_fire("0 * * * *", after=after)
    assert nxt == dt.datetime(2026, 6, 18, 11, 0, tzinfo=dt.UTC)


def test_next_cron_fire_daily_at_nine():
    after = dt.datetime(2026, 6, 18, 12, 0, tzinfo=dt.UTC)
    nxt = _next_cron_fire("0 9 * * *", after=after)
    assert nxt == dt.datetime(2026, 6, 19, 9, 0, tzinfo=dt.UTC)


def test_next_cron_fire_empty_and_invalid_return_none():
    after = dt.datetime(2026, 6, 18, 9, 0, tzinfo=dt.UTC)
    assert _next_cron_fire("", after=after) is None
    assert _next_cron_fire("   ", after=after) is None
    assert _next_cron_fire("not a cron", after=after) is None
