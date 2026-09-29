"""The lifecycle lists on the list contract (#2155).

Deployments, job runs and command runs gain an initiator filter (``"me"``
is the viewer); deployments a trigger kind, a start-time window and a
sort; job runs a status and trigger filter; previews who opened them, why
they failed, a status/opener filter, a sort and per-status counts;
environments a kind, region and owner and a numbered, filtered, searchable
page. Every list is checked against a rival org that reuses the same slugs
and the same people.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_clusters.models import TenantCluster
from astrolift_graphql import UnsupportedSort
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import (
    AppEnvironment,
    CommandRun,
    Deployment,
    PreviewEnvironment,
    ScheduledJobRun,
)
from astrolift_lifecycle.schema.list_contract import (
    CommandRunsFilterInput,
    DeploymentsListFilterInput,
    EnvironmentsListFilterInput,
    PreviewEnvironmentsFilterInput,
    ScheduledJobRunsFilterInput,
)
from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_lifecycle.schema.types import environment_kind
from astrolift_registry.models import RegisteredApp, Workload
from core.cluster_observability import reset_pod_backend_for_tests, set_pod_backend_for_tests
from core.decorators import TenantRequired
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _as(org, user=None):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.pk if user else None))


@pytest.fixture
def people():
    User = get_user_model()
    return SimpleNamespace(
        ana=User.objects.create_user(username="ana", password="x"),
        bo=User.objects.create_user(username="bo", password="x"),
    )


@pytest.fixture
def rival(provider_plugin):
    """A second org reusing the app and environment slugs and the region."""
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
        region="eu-west-1",
        provider_plugin=provider_plugin,
        provider_config={},
        endpoint="https://rival.cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    env = AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="prod")
    return SimpleNamespace(org=org, app=app, cluster=cluster, env=env)


@pytest.fixture(autouse=True)
def _no_pods():
    class _Empty:
        def list_pods(self, *, auth, namespace, app_slug):
            return []

    set_pod_backend_for_tests(_Empty())
    yield
    reset_pod_backend_for_tests()


# ---------------------------------------------------------------------------
# astroliftDeploymentsPage
# ---------------------------------------------------------------------------


def _deploy(app, env, tag, **kwargs):
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=kwargs.pop("trigger_kind", "manual"),
        status=kwargs.pop("status", Deployment.Status.RUNNING.value),
        image_tag=tag,
        **kwargs,
    )


def _tags(page):
    return [d.image_tag for d in page.items]


def _deployments(org, user=None, **kwargs):
    with _as(org, user):
        return LifecycleQuery().astrolift_deployments_page(_info(), **kwargs)


def test_deployments_triggered_by_me(app, env, org, rival, people, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    _deploy(app, env, "mine", triggered_by_user=people.ana)
    _deploy(app, env, "bos", triggered_by_user=people.bo)
    _deploy(app, env, "push", trigger_kind="push")
    _deploy(rival.app, rival.env, "rival-mine", triggered_by_user=people.ana)

    mine = _deployments(org, people.ana, filter=DeploymentsListFilterInput(triggered_by=["me"]))
    assert (_tags(mine), mine.total_count) == (["mine"], 1)
    bos = _deployments(org, people.ana, filter=DeploymentsListFilterInput(triggered_by=[str(people.bo.pk)]))
    assert _tags(bos) == ["bos"]
    junk = _deployments(org, people.ana, filter=DeploymentsListFilterInput(triggered_by=["not-a-user"]))
    assert junk.total_count == 0
    no_viewer = _deployments(org, None, filter=DeploymentsListFilterInput(triggered_by=["me"]))
    assert no_viewer.total_count == 0


def test_deployments_trigger_kind(app, env, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    _deploy(app, env, "a", trigger_kind="push")
    _deploy(app, env, "b", trigger_kind="ci")
    _deploy(app, env, "c", trigger_kind="manual")
    page = _deployments(org, filter=DeploymentsListFilterInput(trigger_kind=["push", "ci"]))
    assert sorted(_tags(page)) == ["a", "b"]
    assert page.total_count == 2


def test_deployments_start_window_reads_created_for_a_queued_deploy(app, env, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    now = timezone.now()
    _deploy(app, env, "old", started_at=now - dt.timedelta(days=3))
    _deploy(app, env, "recent", started_at=now - dt.timedelta(hours=1))
    queued = _deploy(app, env, "queued", status=Deployment.Status.PENDING.value)
    Deployment.objects.filter(pk=queued.pk).update(created_at=now - dt.timedelta(minutes=5))

    since = _deployments(org, filter=DeploymentsListFilterInput(started_after=now - dt.timedelta(days=1)))
    assert sorted(_tags(since)) == ["queued", "recent"]
    before = _deployments(org, filter=DeploymentsListFilterInput(started_before=now - dt.timedelta(days=1)))
    assert _tags(before) == ["old"]


def test_deployments_sort_by_start_walks_every_row(app, env, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    now = timezone.now()
    for n, hours in enumerate([5, 1, 3, 4, 2]):
        _deploy(app, env, f"d{n}", started_at=now - dt.timedelta(hours=hours))
    _deploy(app, env, "never", status=Deployment.Status.PENDING.value)

    tags: list[str] = []
    cursor = None
    for _ in range(10):
        page = _deployments(org, sort="started", limit=2, after=cursor)
        tags.extend(_tags(page))
        cursor = page.next_cursor
        if cursor is None:
            break
    # "never" has no start time and sorts by its creation time, the newest.
    assert tags == ["d0", "d3", "d2", "d4", "d1", "never"]


def test_deployments_sort_change_restarts_the_walk(app, env, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    for n in range(4):
        _deploy(app, env, f"d{n}")
    first = _deployments(org, limit=2)
    restarted = _deployments(org, sort="created", limit=2, after=first.next_cursor)
    assert _tags(restarted) == _tags(_deployments(org, sort="created", limit=2))


def test_deployments_refuse_an_undeclared_or_multi_key_sort(app, env, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    with pytest.raises(UnsupportedSort):
        _deployments(org, sort="status")
    with pytest.raises(UnsupportedSort):
        _deployments(org, sort="-created,started")


# ---------------------------------------------------------------------------
# Job runs and command runs
# ---------------------------------------------------------------------------


@pytest.fixture
def cron(app):
    return Workload.objects.create(
        registered_app=app, name="Nightly", slug="nightly", kind=Workload.Kind.CRONJOB
    )


def test_job_runs_status_and_trigger_filters(app, env, cron, org, rival, people, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)
    ScheduledJobRun.objects.create(workload=cron, app_environment=env, status="succeeded")
    ScheduledJobRun.objects.create(workload=cron, app_environment=env, status="failed")
    manual = ScheduledJobRun.objects.create(
        workload=cron, app_environment=env, status="failed", trigger_kind="manual", triggered_by=people.ana
    )
    rival_cron = Workload.objects.create(
        registered_app=rival.app, name="Nightly", slug="nightly", kind=Workload.Kind.CRONJOB
    )
    ScheduledJobRun.objects.create(
        workload=rival_cron,
        app_environment=rival.env,
        status="failed",
        trigger_kind="manual",
        triggered_by=people.ana,
    )

    def runs(user=None, **filter_kwargs):
        with _as(org, user):
            return LifecycleQuery().astrolift_scheduled_job_runs_page(
                _info(), filter=ScheduledJobRunsFilterInput(**filter_kwargs)
            )

    assert runs(status=["failed"]).total_count == 2
    assert runs(trigger=["scheduled"]).total_count == 2
    manual_runs = runs(trigger=["manual"])
    assert [str(r.id) for r in manual_runs.items] == [str(manual.guid)]
    mine = runs(people.ana, triggered_by=["me"])
    assert (mine.total_count, mine.items[0].triggered_by_me) == (1, True)


def test_command_runs_invoked_by(app, org, rival, people, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)
    CommandRun.objects.create(registered_app=app, invoked_by=people.ana, command=["ls"])
    CommandRun.objects.create(registered_app=app, invoked_by=people.bo, command=["ps"])
    CommandRun.objects.create(registered_app=rival.app, invoked_by=people.ana, command=["cat"])

    def runs(user, values):
        with _as(org, user):
            return LifecycleQuery().astrolift_command_runs_page(
                _info(), filter=CommandRunsFilterInput(invoked_by=values)
            )

    mine = runs(people.ana, ["me"])
    assert [r.command for r in mine.items] == [["ls"]]
    assert (mine.items[0].invoked_by_user_id, mine.items[0].invoked_by_me) == (str(people.ana.pk), True)
    bos = runs(people.ana, [str(people.bo.pk)])
    assert ([r.command for r in bos.items], bos.items[0].invoked_by_me) == ([["ps"]], False)


# ---------------------------------------------------------------------------
# Previews
# ---------------------------------------------------------------------------


def _preview(app, cluster, n, **kwargs):
    env = AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name=f"preview-pr-{n}")
    return PreviewEnvironment.objects.create(
        registered_app=app,
        pr_number=n,
        branch=f"feature-{n}",
        hostname=f"pr-{n}.example.invalid",
        namespace=f"ns-{app.organization.slug}-pr-{n}",
        app_environment=env,
        **kwargs,
    )


def _previews(org, user=None, **kwargs):
    with _as(org, user):
        return LifecycleQuery().astrolift_preview_environments_page(_info(), **kwargs)


def test_previews_opened_by_and_status_filters(app, cluster, org, rival, people, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    _preview(app, cluster, 1, opened_by_login="octocat", status="running")
    _preview(app, cluster, 2, opened_by_login="ana", created_by=people.ana, is_manual=True, status="failed")
    _preview(app, cluster, 3, opened_by_login="hubot", status="failed")
    _preview(rival.app, rival.cluster, 2, opened_by_login="ana", created_by=people.ana, status="failed")

    mine = _previews(org, people.ana, filter=PreviewEnvironmentsFilterInput(opened_by=["me"]))
    assert [p.pr_number for p in mine.items] == [2]
    assert (mine.items[0].opened_by_login, mine.items[0].opened_by_me) == ("ana", True)
    by_login = _previews(org, filter=PreviewEnvironmentsFilterInput(opened_by=["OctoCat"]))
    assert [p.pr_number for p in by_login.items] == [1]
    failed_pr = _previews(org, filter=PreviewEnvironmentsFilterInput(status=["failed"], manual=False))
    assert [p.pr_number for p in failed_pr.items] == [3]


def test_previews_sort_by_ttl(app, cluster, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    now = timezone.now()
    for n, days in [(1, 5), (2, 1), (3, 3)]:
        _preview(app, cluster, n, ttl_until=now + dt.timedelta(days=days))
    page = _previews(org, sort="ttl")
    assert [p.pr_number for p in page.items] == [2, 3, 1]
    with pytest.raises(UnsupportedSort):
        _previews(org, sort="branch")


def test_preview_failure_reason_prefers_the_recorded_one(app, cluster, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    recorded = _preview(app, cluster, 1, status="failed", failure_reason="image build failed\nstep 4 of 9")
    from_deploy = _preview(app, cluster, 2, status="failed")
    _deploy(
        app,
        from_deploy.app_environment,
        "t",
        status=Deployment.Status.FAILED.value,
        aborted_reason="rollout timed out",
    )
    healthy = _preview(app, cluster, 3, status="running", failure_reason="stale text")

    reasons = {p.pr_number: p.failure_reason for p in _previews(org).items}
    assert reasons[recorded.pr_number] == "image build failed"
    assert reasons[from_deploy.pr_number] == "rollout timed out"
    assert reasons[healthy.pr_number] == ""


def test_preview_counts_are_per_status_and_org_scoped(app, cluster, org, rival, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    _preview(app, cluster, 1, status="running")
    _preview(app, cluster, 2, status="running")
    _preview(app, cluster, 3, status="failed")
    _preview(app, cluster, 4, status="torn_down")
    _preview(rival.app, rival.cluster, 1, status="running")

    with _as(org):
        counts = LifecycleQuery().astrolift_preview_environment_counts(_info(), app_slug="hello-app")
    assert (counts.total, counts.building, counts.running, counts.failed, counts.torn_down) == (4, 0, 2, 1, 1)
    with tenant_context(TenantContext(organization_id=None)), pytest.raises(TenantRequired):
        LifecycleQuery().astrolift_preview_environment_counts(_info())


def test_preview_failure_is_kept_and_cleared_on_rebuild(app, cluster):
    from astrolift_workflows.activities.app_lifecycle import (
        _mark_preview_building_sync,
        _mark_preview_failed_sync,
    )

    preview = _preview(app, cluster, 1)
    _mark_preview_failed_sync(preview.pk, "namespace quota exceeded")
    preview.refresh_from_db()
    assert (preview.status, preview.failure_reason) == ("failed", "namespace quota exceeded")
    _mark_preview_building_sync(preview.pk)
    preview.refresh_from_db()
    assert (preview.status, preview.failure_reason) == ("building", "")


def test_pr_context_carries_the_author_login():
    from astrolift_scm.webhook_views import _build_pr_context

    ctx = _build_pr_context(
        {
            "action": "opened",
            "repository": {"full_name": "acme/api"},
            "pull_request": {"number": 7, "head": {"sha": "abc", "ref": "f"}, "user": {"login": "octocat"}},
        }
    )
    assert ctx.author_login == "octocat"


# ---------------------------------------------------------------------------
# astroliftEnvironmentsPage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "kind"),
    [("production", "production"), ("Prod", "production"), ("preview-pr-4", "preview"), ("staging", "other")],
)
def test_environment_kind(name, kind):
    assert environment_kind(name) == kind


@pytest.fixture
def estate(app, cluster, env, org, people, provider_plugin, project, team):
    """``hello-app`` (ana's) with prod, staging, a preview; ``api`` with production on a second cluster."""
    RegisteredApp.objects.filter(pk=app.pk).update(created_by=people.ana)
    TenantCluster.objects.filter(pk=cluster.pk).update(region="eu-west-1")
    AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="staging")
    AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="preview-pr-9", created_by=people.bo
    )
    other = RegisteredApp.objects.create(organization=org, project=project, team=team, name="API", slug="api")
    east = TenantCluster.objects.create(
        organization=org,
        name="east",
        slug="east",
        region="us-east-1",
        provider_plugin=provider_plugin,
        provider_config={},
        endpoint="https://east.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    AppEnvironment.objects.create(registered_app=other, tenant_cluster=east, name="production")
    return SimpleNamespace(app=app, other=other)


def _environments(org, user=None, **kwargs):
    with _as(org, user):
        return LifecycleQuery().astrolift_environments_page(_info(), **kwargs)


def _names(page):
    return [(e.registered_app_slug, e.name) for e in page.items]


def test_environments_page_default_order_and_row_fields(estate, org, rival, people, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    page = _environments(org, people.ana)
    assert _names(page) == [
        ("api", "production"),
        ("hello-app", "preview-pr-9"),
        ("hello-app", "prod"),
        ("hello-app", "staging"),
    ]
    assert (page.total_count, page.page, page.next_cursor) == (4, 1, None)
    rows = {(e.registered_app_slug, e.name): e for e in page.items}
    prod = rows[("hello-app", "prod")]
    assert (prod.kind, prod.region, prod.owner_user_id, prod.owned_by_me) == (
        "production",
        "eu-west-1",
        str(people.ana.pk),
        True,
    )
    preview = rows[("hello-app", "preview-pr-9")]
    assert (preview.kind, preview.owner_user_id, preview.owned_by_me) == ("preview", str(people.bo.pk), False)
    assert rows[("api", "production")].owner_user_id is None


def test_environments_filters_search_and_paging(estate, org, people, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    prods = _environments(org, filter=EnvironmentsListFilterInput(kind=["production"]))
    assert _names(prods) == [("api", "production"), ("hello-app", "prod")]
    east = _environments(org, filter=EnvironmentsListFilterInput(region=["US-EAST-1"]))
    assert _names(east) == [("api", "production")]
    mine = _environments(org, people.ana, filter=EnvironmentsListFilterInput(owner=["me"]))
    assert _names(mine) == [("hello-app", "prod"), ("hello-app", "staging")]
    found = _environments(org, search="stag")
    assert _names(found) == [("hello-app", "staging")]
    by_kind = _environments(org, sort="kind,-name", page=2, page_size=2)
    # other < preview < production, then name Z to A: "production" before "prod".
    assert _names(by_kind) == [("api", "production"), ("hello-app", "prod")]
    assert by_kind.total_count == 4
    with pytest.raises(UnsupportedSort):
        _environments(org, sort="url")


def test_environments_page_never_serves_another_org(estate, org, rival, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    page = _environments(org, search="prod", page_size=100)
    assert str(rival.env.guid) not in {str(e.id) for e in page.items}
    same_region = _environments(org, filter=EnvironmentsListFilterInput(region=["eu-west-1"]))
    assert same_region.total_count == 3
    assert all(e.registered_app_slug in {"hello-app", "api"} for e in page.items)
    with tenant_context(TenantContext(organization_id=None)), pytest.raises(TenantRequired):
        LifecycleQuery().astrolift_environments_page(_info())
