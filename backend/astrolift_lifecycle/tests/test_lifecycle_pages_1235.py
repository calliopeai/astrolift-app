"""Cursor pagination for the lifecycle run / token / preview lists (#1235).

Six list resolvers in ``astrolift_lifecycle.schema.queries`` sliced a hard
cap off the top of an unbounded stream (500 runs, 200 previews, 100 deploy
tokens) with no way to reach the row behind it. For an agent that loops or
a task that runs on every deploy, that window is consumed in days — the
history exists in Postgres and is unreachable from the API, which is the
operator-visible bug behind #1230.

Each converted resolver is covered here for:

* a bounded full walk that serves every row exactly once, terminates, and
  agrees with the DB's own ``(-created_at, -guid)`` ordering;
* ``total_count`` reporting the whole filtered set rather than the page;
* cross-org isolation in BOTH ``items`` and ``total_count`` — the rival org
  reuses the same app / workload / project slugs, so a resolver that
  filtered on slug alone would serve its rows;
* ``search`` narrowing the count, not just the visible page.

Plus the deny-by-default regression this ticket fixes: ``astroliftTaskRuns``
/ ``astroliftAgentRuns`` / ``astroliftScheduledJobRuns`` /
``astroliftCommandRuns`` applied their org clause under ``if org_id is not
None``, so a null tenant left the queryset UNSCOPED (every org's rows)
instead of empty. The clause is now unconditional; ``*_qs_denies_*`` pins it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.utils import timezone

from astrolift_clusters.models import TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import (
    AgentRun,
    AppEnvironment,
    CommandRun,
    DeployToken,
    PreviewEnvironment,
    ScheduledJobRun,
    TaskRun,
)
from astrolift_lifecycle.schema import queries
from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_registry.models import RegisteredApp, Workload
from core.cluster_observability import (
    reset_pod_backend_for_tests,
    set_pod_backend_for_tests,
)
from core.decorators import TenantRequired
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _walk(resolver, org, *, limit, cap=40, **kwargs):
    """Page the whole stream, returning every item id in served order.

    ``cap`` bounds the loop so a walk that never terminates (the classic
    keyset bug: an ORDER BY that disagrees with the seek clause re-serves
    page one forever) fails loudly instead of hanging CI.
    """
    ids: list[str] = []
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=org.id)):
        for _ in range(cap):
            page = resolver(_info(), limit=limit, after=cursor, **kwargs)
            ids.extend(str(item.id) for item in page.items)
            cursor = page.next_cursor
            if cursor is None:
                return ids
    raise AssertionError("walk did not terminate")


def _db_order(qs) -> list[str]:
    """The ordering the seek key claims to implement, straight from the DB.

    Assertions compare against this rather than creation order: rows built
    in a tight loop can share an ``auto_now_add`` timestamp, in which case
    the guid tiebreak — not insertion order — decides.
    """
    return [str(g) for g in qs.order_by("-created_at", "-guid").values_list("guid", flat=True)]


def _assert_covers_once(walked: list[str], expected: list[str]) -> None:
    assert len(walked) == len(set(walked)), "a row was served on more than one page"
    assert walked == expected


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def task_workload(app):
    return Workload.objects.create(
        registered_app=app, name="Migrate", slug="migrate", kind=Workload.Kind.TASK
    )


@pytest.fixture
def agent_workload(app):
    return Workload.objects.create(registered_app=app, name="Triage", slug="triage", kind=Workload.Kind.AGENT)


@pytest.fixture
def cron_workload(app):
    return Workload.objects.create(
        registered_app=app, name="Nightly", slug="nightly", kind=Workload.Kind.CRONJOB
    )


@pytest.fixture
def rival(provider_plugin):
    """A second org that reuses every slug the primary org uses.

    App slugs are unique only *within* a tenant and project slugs only
    within a team, so the collisions are legal — and they are what make
    the isolation assertions load-bearing. A resolver that filtered on
    ``registered_app__slug`` without the org clause would serve these rows
    to the primary org's caller (#1183).
    """
    org = Organization.objects.create(name="Rival", slug="rival-org")
    team = Team.objects.create(organization=org, name="Platform", slug="rival-platform")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    app = RegisteredApp.objects.create(
        organization=org, project=project, team=team, name="Hello", slug="hello-app"
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        name="rival-cluster",
        slug="rival-cluster",
        provider_plugin=provider_plugin,
        provider_config={},
        endpoint="https://rival.cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    env = AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="prod")
    return SimpleNamespace(
        org=org,
        team=team,
        project=project,
        app=app,
        cluster=cluster,
        env=env,
        task_workload=Workload.objects.create(
            registered_app=app, name="Migrate", slug="migrate", kind=Workload.Kind.TASK
        ),
        agent_workload=Workload.objects.create(
            registered_app=app, name="Triage", slug="triage", kind=Workload.Kind.AGENT
        ),
        cron_workload=Workload.objects.create(
            registered_app=app, name="Nightly", slug="nightly", kind=Workload.Kind.CRONJOB
        ),
    )


@pytest.fixture
def pod_calls():
    """Records every live pod listing the preview enrichment performs.

    ``_preview_with_cost`` makes one cluster round-trip per row, so the
    count is the direct measure of whether enrichment ran before or after
    the page slice.
    """
    calls: list[str] = []

    class _CountingPodBackend:
        def list_pods(self, *, auth, namespace, app_slug):
            calls.append(namespace)
            return []

    set_pod_backend_for_tests(_CountingPodBackend())
    yield calls
    reset_pod_backend_for_tests()


# ---------------------------------------------------------------------------
# astroliftTaskRunsPage
# ---------------------------------------------------------------------------


def _task_run(workload, **kwargs):
    return TaskRun.objects.create(
        workload=workload,
        command=kwargs.pop("command", ["python", "manage.py", "migrate"]),
        status=kwargs.pop("status", TaskRun.Status.SUCCEEDED.value),
        **kwargs,
    )


def test_task_runs_walk_reaches_every_row(app, task_workload, org, permission_resolver):
    """The whole stream is reachable, in the DB's own order, with no row
    served twice or dropped at a page boundary."""
    permission_resolver.grant(Permission.APP_READ_LOGS)
    for n in range(23):
        _task_run(task_workload, k8s_job_name=f"migrate-{n:03d}")

    walked = _walk(LifecycleQuery().astrolift_task_runs_page, org, limit=5)
    _assert_covers_once(walked, _db_order(TaskRun.objects.filter(workload=task_workload)))
    assert len(walked) == 23


def test_task_runs_walk_survives_a_shared_timestamp(app, task_workload, org, permission_resolver):
    """Every row written in the same microsecond: the guid tiebreak is the
    only thing keeping the walk from looping or skipping."""
    permission_resolver.grant(Permission.APP_READ_LOGS)
    for n in range(12):
        _task_run(task_workload, k8s_job_name=f"batch-{n:02d}")
    # ``created_at`` is auto_now_add, so a queryset UPDATE is the only way
    # to pin it — .create() / .bulk_create() both overwrite it with now().
    stamp = timezone.now()
    TaskRun.objects.filter(workload=task_workload).update(created_at=stamp)

    walked = _walk(LifecycleQuery().astrolift_task_runs_page, org, limit=4)
    _assert_covers_once(walked, _db_order(TaskRun.objects.filter(workload=task_workload)))
    assert len(walked) == 12


def test_task_runs_total_count_is_the_filtered_set_not_the_page(app, task_workload, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)
    for _ in range(9):
        _task_run(task_workload, status=TaskRun.Status.SUCCEEDED.value)
    for _ in range(3):
        _task_run(task_workload, status=TaskRun.Status.FAILED.value)

    query = LifecycleQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        page = query.astrolift_task_runs_page(_info(), limit=4)
        failed = query.astrolift_task_runs_page(_info(), status=TaskRun.Status.FAILED.value)

    assert len(page.items) == 4
    assert page.total_count == 12
    assert failed.total_count == 3
    assert {i.status for i in failed.items} == {TaskRun.Status.FAILED.value}


def test_task_runs_search_narrows_total_count(app, task_workload, org, permission_resolver):
    """A count that ignored ``search`` would render "5 results" over a
    one-row table."""
    permission_resolver.grant(Permission.APP_READ_LOGS)
    wanted = _task_run(task_workload, k8s_job_name="migrate-7f3ab2")
    for n in range(4):
        _task_run(task_workload, k8s_job_name=f"seed-{n}")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = LifecycleQuery().astrolift_task_runs_page(_info(), search="7f3ab2")

    assert [i.id for i in page.items] == [str(wanted.guid)]
    assert page.total_count == 1


def test_task_runs_search_matches_app_workload_and_status(app, task_workload, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)
    cancelled = _task_run(task_workload, status=TaskRun.Status.CANCELLED.value)
    _task_run(task_workload, status=TaskRun.Status.SUCCEEDED.value)

    query = LifecycleQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        by_app = query.astrolift_task_runs_page(_info(), search="hello-app")
        by_workload = query.astrolift_task_runs_page(_info(), search="migrate")
        by_status = query.astrolift_task_runs_page(_info(), search="cancel")

    assert by_app.total_count == 2
    assert by_workload.total_count == 2
    assert [i.id for i in by_status.items] == [str(cancelled.guid)]


def test_task_runs_other_orgs_runs_are_invisible(app, task_workload, org, rival, permission_resolver):
    """Both orgs' apps are slugged ``hello-app`` and both workloads
    ``migrate``, so only the org clause can tell the streams apart."""
    permission_resolver.grant(Permission.APP_READ_LOGS)
    ours = _task_run(task_workload)
    _task_run(rival.task_workload)

    query = LifecycleQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        unfiltered = query.astrolift_task_runs_page(_info(), limit=50)
        by_slug = query.astrolift_task_runs_page(_info(), app_slug="hello-app", workload_slug="migrate")

    assert [i.id for i in unfiltered.items] == [str(ours.guid)]
    assert unfiltered.total_count == 1, "the count leaked the rival org's row"
    assert [i.id for i in by_slug.items] == [str(ours.guid)]
    assert by_slug.total_count == 1


def test_task_runs_page_and_deprecated_list_field_agree(app, task_workload, org, permission_resolver):
    """Both fields build on ``_task_runs_qs``; this pins that they cannot
    drift into disagreeing about *which rows* are a task run.

    Row SETS, not sequences: the deprecated field orders on ``-created_at``
    alone, so rows sharing a timestamp come back in an arbitrary order
    there while the page walk breaks the tie on guid.
    """
    permission_resolver.grant(Permission.APP_READ_LOGS)
    for n in range(6):
        _task_run(task_workload, status=TaskRun.Status.FAILED.value if n % 2 else "succeeded")

    query = LifecycleQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        listed = query.astrolift_task_runs(_info(), status=TaskRun.Status.FAILED.value, limit=100)
    walked = _walk(query.astrolift_task_runs_page, org, limit=2, status=TaskRun.Status.FAILED.value)

    assert len(walked) == 3
    assert {str(r.id) for r in listed} == set(walked)


# ---------------------------------------------------------------------------
# astroliftAgentRunsPage
# ---------------------------------------------------------------------------


def _agent_run(workload, **kwargs):
    return AgentRun.objects.create(
        workload=workload,
        status=kwargs.pop("status", AgentRun.Status.SUCCEEDED.value),
        **kwargs,
    )


def test_agent_runs_walk_reaches_every_row(app, agent_workload, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    for n in range(17):
        _agent_run(agent_workload, k8s_pod_name=f"triage-{n:03d}")

    walked = _walk(LifecycleQuery().astrolift_agent_runs_page, org, limit=4)
    _assert_covers_once(walked, _db_order(AgentRun.objects.filter(workload=agent_workload)))
    assert len(walked) == 17


def test_agent_runs_total_count_and_status_filter(app, agent_workload, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    for _ in range(8):
        _agent_run(agent_workload, status=AgentRun.Status.SUCCEEDED.value)
    for _ in range(2):
        _agent_run(agent_workload, status=AgentRun.Status.RUNNING.value)

    query = LifecycleQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        page = query.astrolift_agent_runs_page(_info(), limit=3)
        active = query.astrolift_agent_runs_page(_info(), status=AgentRun.Status.RUNNING.value)

    assert len(page.items) == 3
    assert page.total_count == 10
    assert active.total_count == 2
    assert len(active.items) == 2


def test_agent_runs_project_filter_composes_with_paging(app, agent_workload, org, team, permission_resolver):
    """``project_slug`` is the per-project Agents surface's filter; it has
    to narrow the count, not just the first page."""
    permission_resolver.grant(Permission.APP_READ)
    other_project = Project.objects.create(organization=org, team=team, name="Other", slug="other")
    other_app = RegisteredApp.objects.create(
        organization=org, project=other_project, team=team, name="Other", slug="other-app"
    )
    other_workload = Workload.objects.create(
        registered_app=other_app, name="Triage", slug="triage", kind=Workload.Kind.AGENT
    )
    for _ in range(5):
        _agent_run(agent_workload)
    _agent_run(other_workload)

    walked = _walk(LifecycleQuery().astrolift_agent_runs_page, org, limit=2, project_slug="demo")
    with tenant_context(TenantContext(organization_id=org.id)):
        page = LifecycleQuery().astrolift_agent_runs_page(_info(), project_slug="demo", limit=2)

    assert len(walked) == 5
    assert page.total_count == 5


def test_agent_runs_search_narrows_total_count(app, agent_workload, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    wanted = _agent_run(agent_workload, k8s_pod_name="triage-9c1d")
    for n in range(3):
        _agent_run(agent_workload, k8s_pod_name=f"triage-other-{n}")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = LifecycleQuery().astrolift_agent_runs_page(_info(), search="9c1d")

    assert [i.id for i in page.items] == [str(wanted.guid)]
    assert page.total_count == 1


def test_agent_runs_other_orgs_runs_are_invisible(app, agent_workload, org, rival, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    ours = _agent_run(agent_workload)
    _agent_run(rival.agent_workload)

    query = LifecycleQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        unfiltered = query.astrolift_agent_runs_page(_info(), limit=50)
        by_project = query.astrolift_agent_runs_page(_info(), project_slug="demo", limit=50)

    assert [i.id for i in unfiltered.items] == [str(ours.guid)]
    assert unfiltered.total_count == 1, "the count leaked the rival org's row"
    # Both orgs have a project slugged ``demo`` — the filter alone is not a
    # tenant boundary.
    assert [i.id for i in by_project.items] == [str(ours.guid)]
    assert by_project.total_count == 1


# ---------------------------------------------------------------------------
# astroliftScheduledJobRunsPage
# ---------------------------------------------------------------------------


def _cron_run(workload, environment, **kwargs):
    return ScheduledJobRun.objects.create(
        workload=workload,
        app_environment=environment,
        status=kwargs.pop("status", ScheduledJobRun.Status.SUCCEEDED.value),
        **kwargs,
    )


def test_scheduled_job_runs_walk_reaches_every_row(app, cron_workload, env, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)
    for n in range(14):
        _cron_run(cron_workload, env, k8s_job_name=f"nightly-{n:03d}")

    walked = _walk(LifecycleQuery().astrolift_scheduled_job_runs_page, org, limit=3)
    _assert_covers_once(walked, _db_order(ScheduledJobRun.objects.filter(workload=cron_workload)))
    assert len(walked) == 14


def test_scheduled_job_runs_environment_filter_and_total(
    app, cron_workload, env, cluster, org, permission_resolver
):
    permission_resolver.grant(Permission.APP_READ_LOGS)
    staging = AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="staging")
    for _ in range(6):
        _cron_run(cron_workload, env)
    for _ in range(2):
        _cron_run(cron_workload, staging)

    query = LifecycleQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        page = query.astrolift_scheduled_job_runs_page(_info(), limit=3)
        staged = query.astrolift_scheduled_job_runs_page(_info(), environment_name="staging")

    assert len(page.items) == 3
    assert page.total_count == 8
    assert staged.total_count == 2
    assert {i.environment_name for i in staged.items} == {"staging"}


def test_scheduled_job_runs_search_narrows_total_count(app, cron_workload, env, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)
    wanted = _cron_run(cron_workload, env, k8s_job_name="nightly-28471234")
    for n in range(3):
        _cron_run(cron_workload, env, k8s_job_name=f"nightly-1000{n}")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = LifecycleQuery().astrolift_scheduled_job_runs_page(_info(), search="2847")

    assert [i.id for i in page.items] == [str(wanted.guid)]
    assert page.total_count == 1


def test_scheduled_job_runs_other_orgs_runs_are_invisible(
    app, cron_workload, env, org, rival, permission_resolver
):
    permission_resolver.grant(Permission.APP_READ_LOGS)
    ours = _cron_run(cron_workload, env)
    _cron_run(rival.cron_workload, rival.env)

    query = LifecycleQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        page = query.astrolift_scheduled_job_runs_page(_info(), app_slug="hello-app", limit=50)

    assert [i.id for i in page.items] == [str(ours.guid)]
    assert page.total_count == 1, "the count leaked the rival org's row"


# ---------------------------------------------------------------------------
# astroliftCommandRunsPage
# ---------------------------------------------------------------------------


def _command_run(registered_app, **kwargs):
    return CommandRun.objects.create(
        registered_app=registered_app,
        command=kwargs.pop("command", ["bash", "-lc", "ls"]),
        **kwargs,
    )


def test_command_runs_walk_reaches_every_row(app, task_workload, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)
    for n in range(13):
        _command_run(app, workload=task_workload, log_excerpt=f"run {n}")

    walked = _walk(LifecycleQuery().astrolift_command_runs_page, org, limit=5)
    _assert_covers_once(walked, _db_order(CommandRun.objects.filter(registered_app=app)))
    assert len(walked) == 13


def test_command_runs_total_count_is_the_whole_result_set(app, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)
    for _ in range(11):
        _command_run(app)

    with tenant_context(TenantContext(organization_id=org.id)):
        page = LifecycleQuery().astrolift_command_runs_page(_info(), limit=4)

    assert len(page.items) == 4
    assert page.total_count == 11


def test_command_runs_search_matches_the_invoking_operator(
    app, task_workload, org, actor, other_actor, permission_resolver
):
    """Who ran this is the forensic question the exec log exists for, so
    the search has to reach the operator column — and narrow the count
    with it."""
    permission_resolver.grant(Permission.APP_READ_LOGS)
    theirs = _command_run(app, workload=task_workload, invoked_by=other_actor)
    for _ in range(3):
        _command_run(app, workload=task_workload, invoked_by=actor)

    with tenant_context(TenantContext(organization_id=org.id)):
        page = LifecycleQuery().astrolift_command_runs_page(_info(), search="approver@test")

    assert [i.id for i in page.items] == [str(theirs.guid)]
    assert page.total_count == 1


def test_command_runs_other_orgs_runs_are_invisible(app, org, rival, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)
    ours = _command_run(app)
    _command_run(rival.app)

    query = LifecycleQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        page = query.astrolift_command_runs_page(_info(), app_slug="hello-app", limit=50)

    assert [i.id for i in page.items] == [str(ours.guid)]
    assert page.total_count == 1, "the count leaked the rival org's row"


# ---------------------------------------------------------------------------
# astroliftAppDeployTokensPage
# ---------------------------------------------------------------------------


def _deploy_token(registered_app, name, **kwargs):
    return DeployToken.objects.create(
        registered_app=registered_app,
        name=name,
        token_hash=f"hash-{name}",
        token_last_4=kwargs.pop("token_last_4", "0000"),
        **kwargs,
    )


def test_deploy_tokens_walk_reaches_every_row(app, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    for n in range(11):
        _deploy_token(app, f"ci-{n:02d}")

    walked = _walk(LifecycleQuery().astrolift_app_deploy_tokens_page, org, limit=4, app_slug="hello-app")
    _assert_covers_once(walked, _db_order(DeployToken.objects.filter(registered_app=app)))
    assert len(walked) == 11


def test_deploy_tokens_total_count_and_revoked_rows_stay_visible(app, org, permission_resolver):
    """Revoked tokens are audit evidence, not deletions — they page like
    any other row (the resolver filters only soft-deleted ones)."""
    permission_resolver.grant(Permission.APP_READ)
    for n in range(4):
        _deploy_token(app, f"live-{n}")
    _deploy_token(app, "revoked-1", is_revoked=True)

    with tenant_context(TenantContext(organization_id=org.id)):
        page = LifecycleQuery().astrolift_app_deploy_tokens_page(_info(), app_slug="hello-app", limit=2)

    assert len(page.items) == 2
    assert page.total_count == 5


def test_deploy_tokens_soft_deleted_rows_are_hidden_from_page_and_list(app, org, permission_resolver):
    """The list field's ``deleted_at__isnull=True`` clause has to survive
    the conversion — both fields share one queryset builder."""
    permission_resolver.grant(Permission.APP_READ)
    live = _deploy_token(app, "live")
    gone = _deploy_token(app, "gone")
    gone.soft_delete()

    query = LifecycleQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        page = query.astrolift_app_deploy_tokens_page(_info(), app_slug="hello-app", limit=50)
        listed = query.astrolift_app_deploy_tokens(_info(), app_slug="hello-app")

    assert [i.id for i in page.items] == [str(live.guid)]
    assert page.total_count == 1
    assert [str(t.id) for t in listed] == [str(live.guid)]


def test_deploy_tokens_search_matches_name_and_forensic_columns(app, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    runner = _deploy_token(app, "github-actions", token_last_4="9f2c", last_used_ip="10.4.1.9")
    _deploy_token(app, "local-dev", token_last_4="11aa", last_used_ip="10.4.1.10")
    _deploy_token(app, "jenkins", token_last_4="22bb")

    query = LifecycleQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        by_name = query.astrolift_app_deploy_tokens_page(_info(), app_slug="hello-app", search="actions")
        by_last4 = query.astrolift_app_deploy_tokens_page(_info(), app_slug="hello-app", search="9f2c")
        by_ip = query.astrolift_app_deploy_tokens_page(_info(), app_slug="hello-app", search="10.4.1.9")

    assert [i.id for i in by_name.items] == [str(runner.guid)]
    assert by_name.total_count == 1
    assert [i.id for i in by_last4.items] == [str(runner.guid)]
    assert [i.id for i in by_ip.items] == [str(runner.guid)]


def test_deploy_tokens_other_orgs_tokens_are_invisible(app, org, rival, permission_resolver):
    """``app_slug`` is the only filter this resolver takes, and both orgs
    have a ``hello-app`` — without the org clause the rival's CI
    credentials would list here."""
    permission_resolver.grant(Permission.APP_READ)
    ours = _deploy_token(app, "ours")
    _deploy_token(rival.app, "theirs")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = LifecycleQuery().astrolift_app_deploy_tokens_page(_info(), app_slug="hello-app", limit=50)

    assert [i.id for i in page.items] == [str(ours.guid)]
    assert page.total_count == 1, "the count leaked the rival org's token"


# ---------------------------------------------------------------------------
# astroliftPreviewEnvironmentsPage
# ---------------------------------------------------------------------------


def _preview(registered_app, environment, n, **kwargs):
    return PreviewEnvironment.objects.create(
        registered_app=registered_app,
        app_environment=environment,
        pr_number=kwargs.pop("pr_number", n),
        branch=kwargs.pop("branch", f"feature/pr-{n}"),
        hostname=kwargs.pop("hostname", f"pr-{n}-hello.preview.invalid"),
        namespace=kwargs.pop("namespace", f"pr-{n}-hello"),
        status=kwargs.pop("status", PreviewEnvironment.Status.RUNNING.value),
        **kwargs,
    )


def test_preview_environments_walk_reaches_every_row(app, env, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    for n in range(9):
        _preview(app, env, n)

    walked = _walk(LifecycleQuery().astrolift_preview_environments_page, org, limit=2)
    _assert_covers_once(walked, _db_order(PreviewEnvironment.objects.filter(registered_app=app)))
    assert len(walked) == 9


def test_preview_environments_price_only_the_page(app, env, org, permission_resolver, pod_calls):
    """Enrichment runs on the sliced rows, never the whole queryset.

    ``_preview_with_cost`` costs one live cluster round-trip per row, so
    enriching before the slice would make pagination *more* expensive than
    the capped list it replaces."""
    permission_resolver.grant(Permission.APP_READ)
    for n in range(12):
        _preview(app, env, n)

    with tenant_context(TenantContext(organization_id=org.id)):
        page = LifecycleQuery().astrolift_preview_environments_page(_info(), limit=3)

    assert len(page.items) == 3
    assert page.total_count == 12
    assert len(pod_calls) == 3, f"priced {len(pod_calls)} rows to serve a 3-row page"


def test_preview_environments_search_narrows_total_count(app, env, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    wanted = _preview(app, env, 1, branch="feature/keyset-pagination")
    for n in range(2, 5):
        _preview(app, env, n, branch=f"chore/bump-{n}")

    query = LifecycleQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        by_branch = query.astrolift_preview_environments_page(_info(), search="keyset")
        by_host = query.astrolift_preview_environments_page(_info(), search="pr-1-hello")

    assert [i.id for i in by_branch.items] == [str(wanted.guid)]
    assert by_branch.total_count == 1
    assert [i.id for i in by_host.items] == [str(wanted.guid)]
    assert by_host.total_count == 1


def test_preview_environments_other_orgs_previews_are_invisible(app, env, org, rival, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    ours = _preview(app, env, 1)
    _preview(rival.app, rival.env, 1)

    query = LifecycleQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        page = query.astrolift_preview_environments_page(_info(), app_slug="hello-app", limit=50)

    assert [i.id for i in page.items] == [str(ours.guid)]
    assert page.total_count == 1, "the count leaked the rival org's preview"


# ---------------------------------------------------------------------------
# Deny-by-default (#1183) — the leak this conversion closes
# ---------------------------------------------------------------------------


@pytest.fixture
def one_row_of_each(app, env, task_workload, agent_workload, cron_workload):
    """One row per converted stream, so a queryset that fails OPEN returns
    something and the assertions below can tell the difference."""
    _task_run(task_workload)
    _agent_run(agent_workload)
    _cron_run(cron_workload, env)
    _command_run(app)
    _deploy_token(app, "ci")
    _preview(app, env, 1)


@pytest.mark.parametrize(
    "builder,kwargs",
    [
        ("_task_runs_qs", {"app_slug": None, "workload_slug": None}),
        ("_agent_runs_qs", {"app_slug": None, "workload_slug": None, "project_slug": None}),
        ("_scheduled_job_runs_qs", {"app_slug": None, "environment_name": None}),
        ("_command_runs_qs", {"app_slug": None}),
        ("_app_deploy_tokens_qs", {"app_slug": "hello-app"}),
        ("_preview_environments_qs", {"app_slug": None}),
    ],
)
def test_queryset_builders_match_nothing_without_a_tenant(builder, kwargs, one_row_of_each):
    """No tenant context ⇒ ``org_id`` is None ⇒ the queryset matches no rows.

    Four of these six previously applied their org clause under ``if org_id
    is not None``, which left the queryset UNSCOPED in exactly this state —
    a null tenant read every org's runs. ``@tenant_scoped`` rejects first,
    so this is defence in depth, but it is the half that has to hold if the
    builder is ever called from a non-resolver path.
    """
    # Module-level functions, not methods: Strawberry binds a root
    # resolver's ``self`` to the (None) root value, so a builder reached
    # through ``self`` raises AttributeError on every real request.
    qs = getattr(queries, builder)(**kwargs)
    assert qs.count() == 0


@pytest.mark.parametrize(
    "field,permission",
    [
        ("astrolift_task_runs_page", Permission.APP_READ_LOGS),
        ("astrolift_agent_runs_page", Permission.APP_READ),
        ("astrolift_scheduled_job_runs_page", Permission.APP_READ_LOGS),
        ("astrolift_command_runs_page", Permission.APP_READ_LOGS),
        ("astrolift_preview_environments_page", Permission.APP_READ),
    ],
)
def test_page_resolvers_refuse_a_null_tenant(field, permission, one_row_of_each, permission_resolver):
    """Fails closed rather than paging every org's rows (#1183)."""
    permission_resolver.grant(permission)
    with tenant_context(TenantContext(organization_id=None)):
        with pytest.raises(TenantRequired):
            getattr(LifecycleQuery(), field)(_info(), limit=50)


def test_deploy_tokens_page_refuses_a_null_tenant(one_row_of_each, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    with tenant_context(TenantContext(organization_id=None)):
        with pytest.raises(TenantRequired):
            LifecycleQuery().astrolift_app_deploy_tokens_page(_info(), app_slug="hello-app")
