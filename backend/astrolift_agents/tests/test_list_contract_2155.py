"""The Agents area on the list contract (#2155).

Covers the backend gaps the Agents redesign found:

* ``agentFleetPage``: filters, search, multi-key sort, numbered pages and
  the new row columns (status, model source, runtime, clusters, owner).
* ``agentTasksPage``: status list, initiator and trigger filters, sort.
* ``agentUpcomingRuns``: next scheduled firings across the fleet.
* ``retryAgentTask``: the same brief and inputs, refused while in flight.
* ``agentTask(id)``: agentSlug, agentName, projectSlug.
* ``skillsPage`` / ``orgToolDefsPage`` / ``toolDef``: the catalog lists.
* ``agentEnvironmentSpecSecretStatusPage``: paging, filters and a read error.

Every query gets a cross-org case: another org's rows never appear, and a
foreign ``orgId`` is refused.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from graphql import GraphQLError

from astrolift_agents.models import AgentEnvironmentSpec, AgentTask, Brief, Skill, ToolDef
from astrolift_agents.schema.mutations import AgentsMutation
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_agents.schema.types import (
    AgentFleetFilterInput,
    AgentSecretStatusFilterInput,
    AgentTasksFilterInput,
    SkillsFilterInput,
    ToolDefsFilterInput,
)
from astrolift_graphql import UnsupportedSort
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AgentRun, AppEnvironment
from astrolift_registry.models import RegisteredApp, Workload
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx
from core.tests.utils.scope_world import make_cluster

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _info():
    request = SimpleNamespace(user=SimpleNamespace(is_authenticated=False))
    return SimpleNamespace(context=SimpleNamespace(user=None, request=request))


def _as(org, user=None):
    return _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=user.pk if user else None))


def _user(name):
    return get_user_model().objects.create(username=name, email=f"{name}@astrolift.dev")


def _org(slug):
    return Organization.objects.create(name=slug.title(), slug=slug)


def _project(org, slug):
    team = Team.objects.create(organization=org, name=f"{slug} team", slug=f"{slug}-team")
    return Project.objects.create(organization=org, team=team, name=slug.title(), slug=slug), team


def _agent(org, project, team, slug, *, app_slug=None, created_by=None, **kwargs):
    app = RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name=(app_slug or f"{slug}-app").title(),
        slug=app_slug or f"{slug}-app",
        provisioning_status="ready",
        source_repo=f"acme/{slug}",
    )
    return Workload.objects.create(
        registered_app=app,
        name=slug.replace("-", " ").title(),
        slug=slug,
        kind=Workload.Kind.AGENT,
        created_by=created_by,
        **kwargs,
    )


def _spec(org, slug, **kwargs):
    return AgentEnvironmentSpec.objects.create(
        organization=org,
        name=slug,
        slug=slug,
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
        **kwargs,
    )


@pytest.fixture
def fleet():
    """Two orgs; acme has five agents with distinct statuses and columns."""
    viewer = _user("fleet-viewer")
    org = _org("acme2155")
    project, team = _project(org, "demo")
    ops, ops_team = _project(org, "ops")
    cluster = make_cluster(SimpleNamespace(org=org), "f2155")

    running = _agent(org, project, team, "alpha", created_by=viewer)
    AgentRun.objects.create(workload=running, status=AgentRun.Status.RUNNING)
    _spec(org, "alpha", runtime="claude", managed_model=True)
    AppEnvironment.objects.create(registered_app=running.registered_app, name="prod", tenant_cluster=cluster)

    failing = _agent(org, project, team, "bravo")
    AgentRun.objects.create(workload=failing, status=AgentRun.Status.FAILED)
    _spec(org, "bravo", runtime="codex", model_gateway=True)

    scheduled = _agent(
        org, ops, ops_team, "charlie", run_mode=Workload.RunMode.SCHEDULE, run_cron_expression="*/5 * * * *"
    )
    _spec(org, "charlie", runtime="claude")

    paused = _agent(org, ops, ops_team, "delta", run_paused=True)
    idle = _agent(org, ops, ops_team, "echo", run_family=Workload.RunFamily.SERVICE)

    other = _org("other2155")
    other_project, other_team = _project(other, "demo")
    _agent(other, other_project, other_team, "alpha")
    return SimpleNamespace(
        org=org,
        other=other,
        viewer=viewer,
        running=running,
        failing=failing,
        scheduled=scheduled,
        paused=paused,
        idle=idle,
        cluster=cluster,
    )


def _fleet_page(w, **kwargs):
    with _as(w.org, w.viewer):
        return AgentsQuery().agent_fleet_page(_info(), org_id=str(w.org.guid), **kwargs)


# ---------------------------------------------------------------------------
# agentFleetPage
# ---------------------------------------------------------------------------


def test_fleet_numbered_page_carries_the_row_columns(permission_resolver, fleet):
    permission_resolver.grant(Permission.AGENT_READ)
    page = _fleet_page(fleet, sort="name", page=1, page_size=25)

    assert page.total_count == 5
    assert page.page == 1 and page.page_size == 25 and page.next_cursor is None
    by_slug = {r.slug: r for r in page.items}
    assert [r.slug for r in page.items] == ["alpha", "bravo", "charlie", "delta", "echo"]
    assert by_slug["alpha"].status == "running"
    assert by_slug["alpha"].model_source == "managed"
    assert by_slug["alpha"].runtime == "claude"
    assert by_slug["alpha"].environment_spec_slug == "alpha"
    assert by_slug["alpha"].cluster_slugs == [fleet.cluster.slug]
    assert by_slug["alpha"].owner_email == "fleet-viewer@astrolift.dev"
    assert by_slug["alpha"].owned_by_me is True
    assert by_slug["bravo"].status == "failing"
    assert by_slug["bravo"].model_source == "gateway"
    assert by_slug["charlie"].status == "scheduled"
    assert by_slug["charlie"].model_source == "api-key"
    assert by_slug["delta"].status == "paused"
    assert by_slug["echo"].status == "idle"
    assert by_slug["echo"].model_source is None
    assert by_slug["echo"].owner_email == ""


@pytest.mark.parametrize(
    ("filter_input", "expected"),
    [
        (AgentFleetFilterInput(status=["running", "failing"]), {"alpha", "bravo"}),
        (AgentFleetFilterInput(model=["managed"]), {"alpha"}),
        (AgentFleetFilterInput(model=["api-key", "gateway"]), {"bravo", "charlie"}),
        (AgentFleetFilterInput(runtime=["CLAUDE"]), {"alpha", "charlie"}),
        (AgentFleetFilterInput(runtime=["service"]), {"echo"}),
        (AgentFleetFilterInput(project=["OPS"]), {"charlie", "delta", "echo"}),
        (AgentFleetFilterInput(paused=True), {"delta"}),
        (AgentFleetFilterInput(owner=["me"]), {"alpha"}),
        (AgentFleetFilterInput(project=["ops"], status=["idle"]), {"echo"}),
    ],
)
def test_fleet_filters(permission_resolver, fleet, filter_input, expected):
    permission_resolver.grant(Permission.AGENT_READ)
    page = _fleet_page(fleet, filter=filter_input, page=1)
    assert {r.slug for r in page.items} == expected
    assert page.total_count == len(expected)


def test_fleet_cluster_filter_matches_the_apps_environments(permission_resolver, fleet):
    permission_resolver.grant(Permission.AGENT_READ)
    page = _fleet_page(fleet, filter=AgentFleetFilterInput(cluster=[fleet.cluster.slug.upper()]), page=1)
    assert [r.slug for r in page.items] == ["alpha"]


def test_fleet_search_matches_app_project_and_repo(permission_resolver, fleet):
    permission_resolver.grant(Permission.AGENT_READ)
    assert {r.slug for r in _fleet_page(fleet, search="acme/charlie", page=1).items} == {"charlie"}
    assert {r.slug for r in _fleet_page(fleet, search="ops", page=1).items} == {"charlie", "delta", "echo"}


def test_fleet_sorts_by_status_order_then_name_and_pages(permission_resolver, fleet):
    permission_resolver.grant(Permission.AGENT_READ)
    first = _fleet_page(fleet, sort="status,name", page=1, page_size=2)
    second = _fleet_page(fleet, sort="status,name", page=2, page_size=2)
    third = _fleet_page(fleet, sort="status,name", page=3, page_size=2)
    order = [r.slug for r in first.items + second.items + third.items]
    assert order == ["alpha", "bravo", "charlie", "delta", "echo"]
    assert first.total_count == 5
    desc = _fleet_page(fleet, sort="-name", page=1)
    assert [r.slug for r in desc.items][0] == "echo"


def test_fleet_refuses_an_undeclared_sort(permission_resolver, fleet):
    permission_resolver.grant(Permission.AGENT_READ)
    with pytest.raises(UnsupportedSort):
        _fleet_page(fleet, sort="secret", page=1)


def test_fleet_cursor_walk_still_works_and_takes_the_filter(permission_resolver, fleet):
    permission_resolver.grant(Permission.AGENT_READ)
    first = _fleet_page(fleet, limit=2)
    assert first.page is None and first.next_cursor is not None and first.total_count == 5
    rest = _fleet_page(fleet, limit=10, after=first.next_cursor)
    assert len(first.items) + len(rest.items) == 5
    filtered = _fleet_page(fleet, filter=AgentFleetFilterInput(status=["paused"]))
    assert [r.slug for r in filtered.items] == ["delta"]


def test_fleet_never_lists_another_orgs_agents(permission_resolver, fleet):
    permission_resolver.grant(Permission.AGENT_READ)
    page = _fleet_page(fleet, search="alpha", page=1)
    assert [str(r.id) for r in page.items] == [str(fleet.running.guid)]
    with _as(fleet.org, fleet.viewer), pytest.raises(GraphQLError):
        AgentsQuery().agent_fleet_page(_info(), org_id=str(fleet.other.guid), page=1)


def test_fleet_row_queries_do_not_grow_with_the_page(permission_resolver, fleet):
    permission_resolver.grant(Permission.AGENT_READ)
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as small:
        _fleet_page(fleet, sort="name", page=1, page_size=1)
    with CaptureQueriesContext(connection) as large:
        _fleet_page(fleet, sort="name", page=1, page_size=25)
    assert len(large.captured_queries) == len(small.captured_queries)


# ---------------------------------------------------------------------------
# agentTasksPage
# ---------------------------------------------------------------------------


def _task(org, agent, *, status, trigger="manual", by=None, project=None):
    return AgentTask.objects.create(
        organization=org,
        agent_definition=agent,
        project=project,
        status=status,
        trigger_kind=trigger,
        triggered_by_user=by,
    )


@pytest.fixture
def tasks(fleet):
    someone = _user("someone2155")
    t1 = _task(fleet.org, fleet.running, status=AgentTask.Status.RUNNING, by=fleet.viewer)
    t2 = _task(fleet.org, fleet.failing, status=AgentTask.Status.FAILED, by=someone, trigger="api")
    t3 = _task(fleet.org, fleet.scheduled, status=AgentTask.Status.COMPLETED, trigger="schedule")
    other_agent = Workload.objects.get(registered_app__organization=fleet.other, slug="alpha")
    _task(fleet.other, other_agent, status=AgentTask.Status.RUNNING)
    return SimpleNamespace(t1=t1, t2=t2, t3=t3, someone=someone)


def _tasks_page(w, **kwargs):
    with _as(w.org, w.viewer):
        return AgentsQuery().agent_tasks_page(_info(), org_id=str(w.org.guid), **kwargs)


@pytest.mark.parametrize(
    ("filter_input", "expected"),
    [
        (AgentTasksFilterInput(status=["running", "failed"]), {"t1", "t2"}),
        (AgentTasksFilterInput(started_by=["me"]), {"t1"}),
        (AgentTasksFilterInput(started_by_me=True), {"t1"}),
        (AgentTasksFilterInput(started_by_me=False), {"t2", "t3"}),
        (AgentTasksFilterInput(trigger=["schedule"]), {"t3"}),
        (AgentTasksFilterInput(agent=["BRAVO"]), {"t2"}),
    ],
)
def test_tasks_page_filters(permission_resolver, fleet, tasks, filter_input, expected):
    permission_resolver.grant(Permission.AGENT_READ)
    page = _tasks_page(fleet, filter=filter_input)
    names = {
        name
        for name in ("t1", "t2", "t3")
        if str(getattr(tasks, name).guid) in {str(i.id) for i in page.items}
    }
    assert names == expected
    assert page.total_count == len(expected)


def test_tasks_page_started_by_a_user_id(permission_resolver, fleet, tasks):
    permission_resolver.grant(Permission.AGENT_READ)
    page = _tasks_page(fleet, filter=AgentTasksFilterInput(started_by=[str(tasks.someone.pk), "junk"]))
    assert [str(i.id) for i in page.items] == [str(tasks.t2.guid)]


def test_tasks_page_sorts_and_refuses_undeclared_keys(permission_resolver, fleet, tasks):
    permission_resolver.grant(Permission.AGENT_READ)
    oldest = _tasks_page(fleet, sort="created")
    assert [str(i.id) for i in oldest.items] == [str(tasks.t1.guid), str(tasks.t2.guid), str(tasks.t3.guid)]
    newest = _tasks_page(fleet)
    assert [str(i.id) for i in newest.items][0] == str(tasks.t3.guid)
    walk = _tasks_page(fleet, sort="created", limit=1)
    nxt = _tasks_page(fleet, sort="created", limit=1, after=walk.next_cursor)
    assert [str(i.id) for i in nxt.items] == [str(tasks.t2.guid)]
    with pytest.raises(UnsupportedSort):
        _tasks_page(fleet, sort="status")


def test_tasks_page_never_shows_another_orgs_tasks(permission_resolver, fleet, tasks):
    permission_resolver.grant(Permission.AGENT_READ)
    page = _tasks_page(fleet, filter=AgentTasksFilterInput(agent=["alpha"]))
    assert [str(i.id) for i in page.items] == [str(tasks.t1.guid)]
    with _as(fleet.org, fleet.viewer), pytest.raises(GraphQLError):
        AgentsQuery().agent_tasks_page(_info(), org_id=str(fleet.other.guid))


def test_agent_task_carries_agent_and_project_slugs(permission_resolver, fleet):
    permission_resolver.grant(Permission.APP_READ)
    project = fleet.running.registered_app.project
    task = _task(fleet.org, fleet.running, status=AgentTask.Status.COMPLETED, project=project)
    with _as(fleet.org, fleet.viewer):
        row = AgentsQuery().agent_task(_info(), id=str(task.guid))
    assert (row.agent_slug, row.agent_name, row.project_slug) == ("alpha", "Alpha", "demo")


# ---------------------------------------------------------------------------
# agentUpcomingRuns
# ---------------------------------------------------------------------------


def test_upcoming_runs_lists_next_firings_soonest_first(permission_resolver, fleet):
    permission_resolver.grant(Permission.AGENT_READ)
    project, team = _project(fleet.org, "later")
    _agent(
        fleet.org,
        project,
        team,
        "hourly",
        run_mode=Workload.RunMode.SCHEDULE,
        run_cron_expression="0 * * * *",
    )
    _agent(
        fleet.org,
        project,
        team,
        "stopped",
        run_mode=Workload.RunMode.SCHEDULE,
        run_cron_expression="* * * * *",
        run_paused=True,
    )
    _agent(fleet.org, project, team, "broken", run_mode=Workload.RunMode.SCHEDULE, run_cron_expression="nope")
    other_project, other_team = _project(fleet.other, "later")
    _agent(
        fleet.other,
        other_project,
        other_team,
        "foreign",
        run_mode=Workload.RunMode.SCHEDULE,
        run_cron_expression="* * * * *",
    )

    with _as(fleet.org, fleet.viewer):
        page = AgentsQuery().agent_upcoming_runs(_info(), org_id=str(fleet.org.guid), per_agent=2)

    slugs = [r.agent_slug for r in page.items]
    assert set(slugs) == {"charlie", "hourly"}
    assert page.total_count == 4
    times = [r.scheduled_at for r in page.items]
    assert times == sorted(times)
    assert all(t > timezone.now() - dt.timedelta(minutes=1) for t in times)
    assert {r.cron_expression for r in page.items if r.agent_slug == "charlie"} == {"*/5 * * * *"}

    with _as(fleet.org, fleet.viewer):
        narrow = AgentsQuery().agent_upcoming_runs(
            _info(), org_id=str(fleet.org.guid), project=["ops"], page=1, page_size=1
        )
    assert [r.agent_slug for r in narrow.items] == ["charlie"] and narrow.total_count == 1

    with _as(fleet.org, fleet.viewer), pytest.raises(GraphQLError):
        AgentsQuery().agent_upcoming_runs(_info(), org_id=str(fleet.other.guid))


# ---------------------------------------------------------------------------
# retryAgentTask
# ---------------------------------------------------------------------------


@pytest.fixture
def temporal_recorder(monkeypatch):
    starts = []

    def _start(name, args, *, workflow_id, task_queue=None):
        starts.append((name, list(args), workflow_id))
        from astrolift_workflows.client import WorkflowHandle

        return WorkflowHandle(workflow_id=workflow_id, run_id="run", enqueued=True)

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", _start)
    return starts


def _brief(org, digest, status=Brief.Status.READY):
    return Brief.objects.create(organization=org, content_hash=digest, status=status)


def test_retry_runs_the_same_brief_and_inputs(permission_resolver, fleet, temporal_recorder):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    spec = AgentEnvironmentSpec.objects.get(organization=fleet.org, slug="bravo")
    brief = _brief(fleet.org, "a" * 64)
    project = fleet.failing.registered_app.project
    original = AgentTask.objects.create(
        organization=fleet.org,
        agent_definition=fleet.failing,
        environment_spec=spec,
        brief=brief,
        project=project,
        status=AgentTask.Status.FAILED,
        dispatch_input={"prompt": "again"},
        timeout_seconds=900,
    )
    # The agent's own package moved on; the retry must not pick it up.
    fleet.failing.brief = _brief(fleet.org, "b" * 64)
    fleet.failing.save()

    with _as(fleet.org, fleet.viewer):
        result = AgentsMutation().retry_agent_task(_info(), id=str(original.guid))

    assert result.ok is True, result.errors
    retried = AgentTask.objects.get(guid=str(result.data.id))
    assert retried.pk != original.pk
    assert retried.brief_id == brief.pk
    assert retried.environment_spec_id == spec.pk
    assert retried.dispatch_input == {"prompt": "again"}
    assert retried.timeout_seconds == 900
    assert retried.project_id == project.pk
    assert retried.triggered_by_user_id == fleet.viewer.pk
    assert retried.status == AgentTask.Status.QUEUED
    assert [name for name, _, _ in temporal_recorder] == ["DispatchAgentTaskWorkflow"]


def test_retry_refuses_in_flight_and_orphaned_tasks(permission_resolver, fleet, temporal_recorder):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    brief = _brief(fleet.org, "c" * 64)
    running = AgentTask.objects.create(
        organization=fleet.org, agent_definition=fleet.running, brief=brief, status=AgentTask.Status.RUNNING
    )
    no_agent = AgentTask.objects.create(organization=fleet.org, brief=brief, status=AgentTask.Status.FAILED)
    revoked = AgentTask.objects.create(
        organization=fleet.org,
        agent_definition=fleet.running,
        brief=_brief(fleet.org, "d" * 64, status=Brief.Status.REVOKED),
        status=AgentTask.Status.FAILED,
    )
    with _as(fleet.org, fleet.viewer):
        for task in (running, no_agent, revoked):
            result = AgentsMutation().retry_agent_task(_info(), id=str(task.guid))
            assert result.ok is False
            assert result.errors[0].code == ErrorCode.PRECONDITION.value
    assert temporal_recorder == []


def test_retry_cannot_reach_another_orgs_task(permission_resolver, fleet, temporal_recorder):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    other_agent = Workload.objects.get(registered_app__organization=fleet.other, slug="alpha")
    foreign = AgentTask.objects.create(
        organization=fleet.other,
        agent_definition=other_agent,
        brief=_brief(fleet.other, "e" * 64),
        status=AgentTask.Status.FAILED,
    )
    with _as(fleet.org, fleet.viewer):
        result = AgentsMutation().retry_agent_task(_info(), id=str(foreign.guid))
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value
    assert AgentTask.objects.filter(organization=fleet.org).count() == 0
    assert temporal_recorder == []


# ---------------------------------------------------------------------------
# Catalog: skillsPage, orgToolDefsPage, toolDef
# ---------------------------------------------------------------------------


@pytest.fixture
def catalog(fleet):
    mine = Skill.objects.create(
        organization=fleet.org, name="Review", slug="review", created_by=fleet.viewer, agent_type="claude"
    )
    imported = Skill.objects.create(
        organization=fleet.org,
        name="Deploy",
        slug="deploy",
        source_kind=Skill.SourceKind.REPO_IMPORT,
        source_ref="acme/skills@main",
    )
    glob = Skill.objects.create(organization=None, name="Git", slug="git", is_global=True)
    foreign = Skill.objects.create(organization=fleet.other, name="Secret", slug="secret")
    tools = SimpleNamespace(
        lint=ToolDef.objects.create(
            skill=mine, name="Lint", slug="lint", is_builtin=False, created_by=fleet.viewer
        ),
        ship=ToolDef.objects.create(
            skill=imported, name="Ship", slug="ship", adapter="http_endpoint", is_builtin=False
        ),
        clone=ToolDef.objects.create(skill=glob, name="Clone", slug="clone", is_builtin=True),
        leak=ToolDef.objects.create(skill=foreign, name="Leak", slug="leak", is_builtin=False),
    )
    return SimpleNamespace(mine=mine, imported=imported, glob=glob, foreign=foreign, tools=tools)


def _skills(w, **kwargs):
    with _as(w.org, w.viewer):
        return AgentsQuery().skills_page(_info(), org_id=str(w.org.guid), **kwargs)


def test_skills_page_lists_own_and_global_with_new_columns(permission_resolver, fleet, catalog):
    permission_resolver.grant(Permission.APP_READ)
    page = _skills(fleet, sort="name")
    assert [s.slug for s in page.items] == ["deploy", "git", "review"]
    assert page.total_count == 3 and page.page == 1
    by_slug = {s.slug: s for s in page.items}
    assert by_slug["review"].created_by_email == "fleet-viewer@astrolift.dev"
    assert by_slug["review"].created_by_me is True
    assert by_slug["deploy"].is_imported is True
    assert by_slug["deploy"].source_kind == "repo_import"
    assert by_slug["deploy"].source_ref == "acme/skills@main"
    assert by_slug["git"].is_imported is False


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"filter": SkillsFilterInput(scope=["global"])}, ["git"]),
        ({"filter": SkillsFilterInput(scope=["org"])}, ["deploy", "review"]),
        ({"filter": SkillsFilterInput(imported=True)}, ["deploy"]),
        ({"filter": SkillsFilterInput(imported=False)}, ["git", "review"]),
        ({"filter": SkillsFilterInput(created_by=["me"])}, ["review"]),
        ({"filter": SkillsFilterInput(agent_type=["claude"])}, ["review"]),
        ({"search": "acme/skills"}, ["deploy"]),
        ({"search": "secret"}, []),
    ],
)
def test_skills_page_filters_and_search(permission_resolver, fleet, catalog, kwargs, expected):
    permission_resolver.grant(Permission.APP_READ)
    assert [s.slug for s in _skills(fleet, sort="slug", **kwargs).items] == expected


def test_skills_page_refuses_a_foreign_org(permission_resolver, fleet, catalog):
    permission_resolver.grant(Permission.APP_READ)
    with _as(fleet.org, fleet.viewer), pytest.raises(GraphQLError):
        AgentsQuery().skills_page(_info(), org_id=str(fleet.other.guid))


def _tools(w, **kwargs):
    with _as(w.org, w.viewer):
        return AgentsQuery().org_tool_defs_page(_info(), org_id=str(w.org.guid), **kwargs)


def test_tool_defs_page_carries_parent_skill_and_builtin(permission_resolver, fleet, catalog):
    permission_resolver.grant(Permission.APP_READ)
    page = _tools(fleet)
    assert [t.slug for t in page.items] == ["ship", "clone", "lint"]
    by_slug = {t.slug: t for t in page.items}
    assert (by_slug["lint"].skill_slug, by_slug["lint"].skill_name) == ("review", "Review")
    assert str(by_slug["lint"].skill_id) == str(catalog.mine.guid)
    assert by_slug["lint"].created_by_me is True
    assert by_slug["clone"].is_builtin is True and by_slug["clone"].skill_is_global is True


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"filter": ToolDefsFilterInput(builtin=True)}, ["clone"]),
        ({"filter": ToolDefsFilterInput(skill=["REVIEW"])}, ["lint"]),
        ({"filter": ToolDefsFilterInput(adapter=["http_endpoint"])}, ["ship"]),
        ({"filter": ToolDefsFilterInput(scope=["global"])}, ["clone"]),
        ({"filter": ToolDefsFilterInput(created_by=["me"])}, ["lint"]),
        ({"search": "leak"}, []),
    ],
)
def test_tool_defs_page_filters(permission_resolver, fleet, catalog, kwargs, expected):
    permission_resolver.grant(Permission.APP_READ)
    assert [t.slug for t in _tools(fleet, sort="slug", **kwargs).items] == expected


def test_tool_def_reads_one_and_hides_another_orgs(permission_resolver, fleet, catalog):
    permission_resolver.grant(Permission.APP_READ)
    with _as(fleet.org, fleet.viewer):
        q = AgentsQuery()
        assert q.tool_def(_info(), id=str(catalog.tools.lint.guid)).skill_slug == "review"
        assert q.tool_def(_info(), id=str(catalog.tools.clone.guid)) is not None
        assert q.tool_def(_info(), id=str(catalog.tools.leak.guid)) is None
        assert q.tool_def(_info(), id="not-a-guid") is None
    catalog.mine.soft_delete()
    with _as(fleet.org, fleet.viewer):
        assert AgentsQuery().tool_def(_info(), id=str(catalog.tools.lint.guid)) is None


def test_skill_import_records_source_and_creator(fleet):
    from astrolift_agents.services.skill_importer import _upsert_skill

    skill = _upsert_skill(
        organization=fleet.org,
        slug="fresh",
        cfg={"name": "Fresh"},
        source_ref="acme/lib@main",
        created_by_id=fleet.viewer.pk,
    )
    assert (skill.source_kind, skill.source_ref, skill.created_by_id) == (
        "repo_import",
        "acme/lib@main",
        fleet.viewer.pk,
    )
    # A re-import by someone else keeps the author.
    again = _upsert_skill(organization=fleet.org, slug="fresh", cfg={"name": "Fresh"}, created_by_id=None)
    assert again.created_by_id == fleet.viewer.pk


def test_create_skill_records_its_creator(permission_resolver, fleet):
    from astrolift_agents.schema.mutations import SkillInput

    permission_resolver.grant(Permission.APP_CREATE)
    with _as(fleet.org, fleet.viewer):
        result = AgentsMutation().create_skill(
            _info(),
            input=SkillInput(name="Mine", slug="mine", description="", content=""),
            org_id=str(fleet.org.guid),
        )
    assert result.ok is True
    assert Skill.objects.get(guid=str(result.data.id)).created_by_id == fleet.viewer.pk
    assert result.data.created_by_me is True


# ---------------------------------------------------------------------------
# agentEnvironmentSpecSecretStatusPage
# ---------------------------------------------------------------------------


@pytest.fixture
def probed(monkeypatch, fleet):
    """Probe results without a secret store; the cluster resolves or not."""
    state = SimpleNamespace(cluster=fleet.cluster)

    def _resolve(org):
        from astrolift_agents.services.agent_cluster import NoAgentClusterError

        if state.cluster is None:
            raise NoAgentClusterError("the organization has no agent cluster")
        return state.cluster

    def _probe(*, cluster, refs, unscoped=None):
        return [
            {"env_var": "B_TOKEN", "uri": "vault://b", "exists": True, "error": None, "provider": "vault"},
            {"env_var": "A_KEY", "uri": "vault://a", "exists": False, "error": "denied", "provider": "vault"},
            {"env_var": "C_URL", "uri": "aws://c", "exists": True, "error": None, "provider": "aws"},
        ]

    monkeypatch.setattr("astrolift_agents.services.agent_cluster.resolve_agent_cluster", _resolve)
    monkeypatch.setattr("astrolift_dispatch.agent_secrets.probe_ref_statuses", _probe)
    monkeypatch.setattr("astrolift_dispatch.agent_secrets.resolve_secrets_backend", lambda cluster: object())
    return state


def _secrets(w, slug, **kwargs):
    with _as(w.org, w.viewer):
        return AgentsQuery().agent_environment_spec_secret_status_page(_info(), slug=slug, **kwargs)


def test_secret_status_page_sorts_filters_and_pages(permission_resolver, fleet, probed):
    permission_resolver.grant(Permission.SECRET_LIST)
    page = _secrets(fleet, "alpha")
    assert [r.env_var for r in page.items] == ["A_KEY", "B_TOKEN", "C_URL"]
    assert page.error is None and page.total_count == 3
    assert [r.env_var for r in _secrets(fleet, "alpha", sort="-exists,envVar").items] == [
        "B_TOKEN",
        "C_URL",
        "A_KEY",
    ]
    assert [
        r.env_var for r in _secrets(fleet, "alpha", filter=AgentSecretStatusFilterInput(exists=True)).items
    ] == [
        "B_TOKEN",
        "C_URL",
    ]
    assert [
        r.env_var for r in _secrets(fleet, "alpha", filter=AgentSecretStatusFilterInput(failing=True)).items
    ] == ["A_KEY"]
    assert [
        r.env_var
        for r in _secrets(fleet, "alpha", filter=AgentSecretStatusFilterInput(provider=["AWS"])).items
    ] == ["C_URL"]
    assert [r.env_var for r in _secrets(fleet, "alpha", search="vault://b").items] == ["B_TOKEN"]
    second = _secrets(fleet, "alpha", page=2, page_size=2)
    assert [r.env_var for r in second.items] == ["C_URL"] and second.total_count == 3 and second.page == 2
    with pytest.raises(UnsupportedSort):
        _secrets(fleet, "alpha", sort="value")


def test_secret_status_page_reports_why_the_read_failed(permission_resolver, fleet, probed):
    permission_resolver.grant(Permission.SECRET_LIST)
    missing = _secrets(fleet, "nope")
    assert missing.items == [] and missing.error == "environment spec not found"
    probed.cluster = None
    no_cluster = _secrets(fleet, "alpha")
    assert no_cluster.error == "the organization has no agent cluster"


def test_secret_status_page_reads_another_orgs_spec_as_missing(permission_resolver, fleet, probed):
    permission_resolver.grant(Permission.SECRET_LIST)
    _spec(fleet.other, "hidden")
    page = _secrets(fleet, "hidden")
    assert page.items == [] and page.error == "environment spec not found"
