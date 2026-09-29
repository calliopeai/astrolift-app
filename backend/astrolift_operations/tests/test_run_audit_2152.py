"""``astroliftRunAudit``: one cursor list over everything that ran (#2152).

Pinned here: the five kinds merge into one newest-first walk that serves
every row once and terminates, with an exact count; each filter narrows
rows and count; each kind is read under its own permission and narrowed
to the caller's scopes; another org's runs never show; who and what
started each run comes through; and the page reads its rows in a bounded
number of queries.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from astrolift_agents.models import AgentTask
from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_graphql import UnsupportedSort
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment, Deployment, ScheduledJobRun, TaskRun
from astrolift_operations.models import WorkflowRun
from astrolift_operations.schema.queries import OperationsQuery
from astrolift_operations.schema.run_audit import RunAuditFilterInput
from astrolift_registry.models import RegisteredApp, Workload
from core.decorators import TenantRequired
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind
from core.tenancy import TenantContext, tenant_context
from workflows.models import WorkflowDefinition

pytestmark = pytest.mark.django_db

T0 = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)
ALL_READS = (Permission.AGENT_READ, Permission.WORKFLOW_READ, Permission.APP_READ, Permission.APP_READ_LOGS)


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None))


def _plugin():
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="runs-2152",
        defaults={
            "name": "Runs",
            "plugin_version": "0.0.1",
            "capabilities_manifest": {},
            "config_schema": {},
        },
    )
    return plugin


def _tenant(slug: str):
    """An org with one project, one app, one env and the three workload kinds."""
    org = Organization.objects.create(name=slug, slug=slug)
    team = Team.objects.create(organization=org, name=f"{slug} team", slug=f"{slug}-team")
    project = Project.objects.create(organization=org, team=team, name="Payments", slug="payments")
    app = RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name="Checkout",
        slug="checkout",
        provisioning_status="ready",
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        name=f"{slug}-c",
        slug=f"{slug}-c",
        provider_plugin=_plugin(),
        provider_config={},
        endpoint="https://c.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    env = AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="prod")
    return SimpleNamespace(
        org=org,
        team=team,
        project=project,
        app=app,
        env=env,
        agent=Workload.objects.create(
            registered_app=app, name="Triage", slug="triage", kind=Workload.Kind.AGENT
        ),
        job=Workload.objects.create(
            registered_app=app, name="Nightly", slug="nightly", kind=Workload.Kind.CRONJOB
        ),
        task=Workload.objects.create(
            registered_app=app, name="Migrate", slug="migrate", kind=Workload.Kind.TASK
        ),
        definition=WorkflowDefinition.objects.create(
            organization=org,
            project=project,
            name="Release train",
            slug="release-train",
            model_label="workflows.workflowdefinition",
            states=[{"name": "pending", "label": "Pending", "is_initial": True, "is_final": False}],
            transitions=[],
            is_enabled=True,
        ),
    )


def _runs(t, user, *, minutes: int = 0):
    """One run of every kind, each a minute apart starting ``minutes`` after T0."""
    at = [T0 + dt.timedelta(minutes=minutes + i) for i in range(5)]
    return SimpleNamespace(
        agent=AgentTask.objects.create(
            organization=t.org,
            project=t.project,
            agent_definition=t.agent,
            status=AgentTask.Status.RUNNING,
            started_at=at[0],
            triggered_by_user=user,
            trigger_kind="manual",
        ),
        workflow=WorkflowRun.objects.create(
            workflow_kind="WorkflowDefinitionRunWorkflow",
            workflow_definition=t.definition,
            workflow_id=f"wf-{t.org.slug}-{minutes}",
            run_id="",
            organization=t.org,
            status=WorkflowRun.Status.COMPLETED,
            started_at=at[1],
            ended_at=at[1] + dt.timedelta(seconds=90),
            trigger_kind="schedule",
        ),
        deployment=Deployment.objects.create(
            registered_app=t.app,
            app_environment=t.env,
            trigger_kind=Deployment.TriggerKind.PUSH,
            status=Deployment.Status.FAILED,
            started_at=at[2],
            commit_author="dana",
        ),
        job=ScheduledJobRun.objects.create(
            workload=t.job, app_environment=t.env, status=ScheduledJobRun.Status.SUCCEEDED, started_at=at[3]
        ),
        task=TaskRun.objects.create(
            workload=t.task,
            app_environment=t.env,
            status=TaskRun.Status.PENDING,
            trigger_kind=TaskRun.TriggerKind.API,
            triggered_by_user=user,
            started_at=at[4],
        ),
    )


@pytest.fixture
def world():
    User = get_user_model()
    alice = User.objects.create(username="alice-2152", email="alice-2152@example.test", first_name="Alice")
    bob = User.objects.create(username="bob-2152", email="bob-2152@example.test")
    acme = _tenant("acme-2152")
    rival = _tenant("rival-2152")
    first = _runs(acme, alice, minutes=0)
    second = _runs(acme, bob, minutes=10)
    _runs(rival, alice, minutes=20)
    return SimpleNamespace(acme=acme, alice=alice, bob=bob, first=first, second=second)


def _audit(world, *, viewer=None, **kwargs):
    with tenant_context(TenantContext(organization_id=world.acme.org.id, actor_user_id=viewer)):
        return OperationsQuery().astrolift_run_audit(_info(), **kwargs)


def _grant_all(permission_resolver):
    for permission in ALL_READS:
        permission_resolver.grant(permission)


def _keys(page) -> list[str]:
    return [f"{i.kind}:{i.id}" for i in page.items]


def test_walk_merges_every_kind_newest_first_once(permission_resolver, world):
    _grant_all(permission_resolver)
    first = _audit(world, first=100)
    assert first.total_count == 10
    assert [i.kind for i in first.items] == ["task", "job", "deployment", "workflow", "agent"] * 2
    ats = [i.at for i in first.items]
    assert ats == sorted(ats, reverse=True)

    walked: list[str] = []
    cursor = None
    for _ in range(20):
        page = _audit(world, first=3, after=cursor)
        assert page.total_count == 10
        walked.extend(_keys(page))
        cursor = page.next_cursor
        if cursor is None:
            break
    assert walked == _keys(first)

    oldest = _audit(world, first=100, sort="at")
    assert _keys(oldest) == list(reversed(_keys(first)))
    with pytest.raises(UnsupportedSort):
        _audit(world, sort="-took")


def test_ties_on_time_break_by_kind_and_guid_across_pages(permission_resolver, world):
    """Every kind at the same instant: the tiebreak keeps the walk total."""
    _grant_all(permission_resolver)
    for model in (AgentTask, WorkflowRun, Deployment, ScheduledJobRun, TaskRun):
        model.objects.update(started_at=T0)
    everything = _keys(_audit(world, first=100))
    walked: list[str] = []
    cursor = None
    for _ in range(20):
        page = _audit(world, first=2, after=cursor)
        walked.extend(_keys(page))
        cursor = page.next_cursor
        if cursor is None:
            break
    assert walked == everything
    assert len(set(walked)) == 10


def test_items_carry_who_and_what_started_them(permission_resolver, world):
    _grant_all(permission_resolver)
    items = {i.kind: i for i in _audit(world, viewer=world.alice.pk, first=5, sort="at").items}
    agent = items["agent"]
    assert (agent.trigger, agent.started_by_kind, agent.started_by_display) == ("manual", "user", "Alice")
    assert agent.started_by_id == str(world.alice.pk) and agent.started_by_me is True
    assert (agent.agent_slug, agent.project_slug, agent.app_slug) == ("triage", "payments", "checkout")
    workflow = items["workflow"]
    assert (workflow.trigger, workflow.started_by_kind, workflow.started_by_id) == (
        "schedule",
        "schedule",
        None,
    )
    assert workflow.workflow_slug == "release-train" and workflow.duration_seconds == 90
    assert workflow.outcome == "succeeded"
    deployment = items["deployment"]
    assert (deployment.trigger, deployment.source_trigger, deployment.started_by_kind) == (
        "webhook",
        "push",
        "webhook",
    )
    assert deployment.started_by_display == "dana" and deployment.outcome == "failed"
    assert deployment.subject == "checkout · prod"
    job = items["job"]
    assert (job.trigger, job.started_by_kind, job.scope) == ("schedule", "schedule", "checkout · prod")
    task = items["task"]
    assert (task.trigger, task.started_by_kind, task.outcome) == ("api", "token", "waiting")


def test_filters_narrow_rows_and_count(permission_resolver, world):
    _grant_all(permission_resolver)

    def count(**f):
        return _audit(world, first=100, filter=RunAuditFilterInput(**f)).total_count

    assert count(kind=["agent", "job"]) == 4
    assert count(outcome=["failed"]) == 2
    assert count(status=["running"]) == 2
    assert count(agent=["triage"]) == 2
    assert count(workflow=["release-train"]) == 2
    assert count(project=["payments"]) == 10
    assert count(project=["elsewhere"]) == 0
    # Workflow runs of a definition belong to its project, not an app.
    assert count(app=["checkout"]) == 8
    assert count(trigger=["schedule"]) == 4
    assert count(trigger=["webhook", "api"]) == 4
    assert count(started_by=[str(world.bob.pk)]) == 2
    assert count(started_by=["not-a-pk"]) == 0
    assert count(since=T0 + dt.timedelta(minutes=10)) == 5
    assert count(since=T0 + dt.timedelta(minutes=10), until=T0 + dt.timedelta(minutes=11)) == 2
    with tenant_context(TenantContext(organization_id=world.acme.org.id, actor_user_id=world.bob.pk)):
        mine = OperationsQuery().astrolift_run_audit(_info(), filter=RunAuditFilterInput(started_by=["me"]))
    assert {i.kind for i in mine.items} == {"agent", "task"}
    assert all(i.started_by_me for i in mine.items)


def test_search_matches_guid_prefix_subject_and_initiator(permission_resolver, world):
    _grant_all(permission_resolver)
    guid = str(world.second.deployment.guid)
    assert _keys(_audit(world, search=guid)) == [f"deployment:{guid}"]
    assert _audit(world, search="release").total_count == 2
    assert _audit(world, search="bob-2152").total_count == 2
    assert _audit(world, search="dana").total_count == 2


def test_each_kind_needs_its_own_read(permission_resolver, world):
    permission_resolver.grant(Permission.WORKFLOW_READ)
    page = _audit(world, first=100)
    assert {i.kind for i in page.items} == {"workflow"}
    assert page.total_count == 2

    permission_resolver.grant(Permission.APP_READ)
    assert {i.kind for i in _audit(world, first=100).items} == {"workflow", "deployment"}


def test_no_read_at_all_is_refused(permission_resolver, world):
    with pytest.raises(PermissionDenied):
        _audit(world)


def test_app_kinds_narrow_to_the_apps_the_caller_covers(permission_resolver, world):
    other_app = RegisteredApp.objects.create(
        organization=world.acme.org,
        team=world.acme.team,
        name="Other",
        slug="other-app",
        provisioning_status="ready",
    )
    permission_resolver.grant(Permission.APP_READ, scope=PermissionScope(kind=ScopeKind.APP, id=other_app.pk))
    page = _audit(world, first=100)
    assert page.items == [] and page.total_count == 0

    permission_resolver.grant(
        Permission.APP_READ, scope=PermissionScope(kind=ScopeKind.APP, id=world.acme.app.pk)
    )
    assert {i.kind for i in _audit(world, first=100).items} == {"deployment"}


def test_no_tenant_is_refused(permission_resolver, world):
    _grant_all(permission_resolver)
    with tenant_context(TenantContext(organization_id=None)), pytest.raises(TenantRequired):
        OperationsQuery().astrolift_run_audit(_info())


def test_page_reads_are_bounded(permission_resolver, world):
    _grant_all(permission_resolver)

    def queries(n):
        with CaptureQueriesContext(connection) as ctx:
            _audit(world, first=n)
        return len(ctx.captured_queries)

    small = queries(2)
    for i in range(6):
        _runs(world.acme, world.alice, minutes=100 + i * 10)
    assert queries(40) == small + 3  # the extra kinds on the larger page, one query each


def test_new_runs_default_to_now_when_not_started(permission_resolver, world):
    """A run that has not started sorts by its creation time."""
    _grant_all(permission_resolver)
    queued = AgentTask.objects.create(
        organization=world.acme.org, agent_definition=world.acme.agent, trigger_kind="api"
    )
    top = _audit(world, first=1).items[0]
    assert (top.kind, top.id) == ("agent", str(queued.guid))
    assert top.started_at is None and top.at <= timezone.now()


def test_both_lists_execute_through_the_schema(permission_resolver, world):
    """The new arguments and types bind in the real schema (#2151, #2152)."""
    from config.schema import schema

    for permission in Permission:
        permission_resolver.grant(permission)
    document = """
    query($f: AstroliftRunAuditFilter, $a: AstroliftAuditEventsFilter) {
      astroliftRunAudit(filter: $f, search: "", sort: "-at", first: 3) {
        items { kind id subject scope trigger sourceTrigger startedByKind startedById
                startedByDisplay startedByMe at startedAt endedAt durationSeconds status outcome
                agentSlug workflowSlug projectSlug appSlug environmentName }
        nextCursor totalCount page pageSize
      }
      astroliftAuditEventsPage(search: "role", targetKind: "user", targetId: "1",
                               subjectUserId: "1", filter: $a, sort: "-occurredAt", includeTotal: true) {
        items { id } nextCursor totalCount
      }
    }
    """
    variables = {
        "f": {"kind": ["agent", "deployment"], "startedBy": ["me"], "trigger": ["manual"]},
        "a": {"targetKind": ["user"], "subjectUser": "me", "since": "2026-01-01T00:00:00Z"},
    }
    with tenant_context(TenantContext(organization_id=world.acme.org.id, actor_user_id=world.alice.pk)):
        result = schema.execute_sync(document, variable_values=variables, context_value=_ctx(world))
    assert result.errors is None, result.errors
    runs = result.data["astroliftRunAudit"]
    assert runs["totalCount"] == 1
    assert runs["items"][0]["kind"] == "agent" and runs["items"][0]["startedByMe"] is True
    assert runs["page"] is None
    with tenant_context(TenantContext(organization_id=world.acme.org.id, actor_user_id=world.alice.pk)):
        refused = schema.execute_sync(
            '{ astroliftRunAudit(sort: "name") { totalCount } }', context_value=_ctx(world)
        )
    assert refused.errors and "not available" in refused.errors[0].message


def _ctx(world):
    from core.schema.context import StrawberryContext

    class _Request:
        user = world.alice
        session: dict = {}
        headers: dict = {}

    return StrawberryContext(_Request())
