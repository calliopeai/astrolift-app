"""The Workloads list on the list contract (#2155).

``astroliftWorkloadsPage`` gains ``filter`` (kind, isPublic, app, owner),
a multi-key ``sort`` with numbered paging and a filtered ``totalCount``,
an owner on the row, and each cron job's latest run.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from astrolift_clusters.models import TenantCluster
from astrolift_graphql import UnsupportedSort
from astrolift_lifecycle.models import AppEnvironment, ScheduledJobRun
from astrolift_registry.models import Workload
from astrolift_registry.schema.queries import RegistryQuery
from astrolift_registry.schema.workload_list import WorkloadsListFilterInput
from astrolift_registry.tests.test_registry_pages_1235 import _scaffold, _second_app, _workload
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _page(org, viewer=None, **kwargs):
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=viewer.pk if viewer else None)):
        return RegistryQuery().astrolift_workloads_page(_info(), **kwargs)


@pytest.fixture
def people():
    User = get_user_model()
    return SimpleNamespace(
        ana=User.objects.create_user(username="ana", password="x"),
        bo=User.objects.create_user(username="bo", password="x"),
    )


@pytest.fixture
def fleet(permission_resolver, people):
    """Two apps in one org, one created by ana, plus a rival org reusing every slug."""
    permission_resolver.grant(Permission.APP_READ)
    org, team, app = _scaffold()
    app.created_by = people.ana
    app.save(update_fields=["created_by"])
    other = _second_app(org, team, slug="other-app")
    rows = {
        "web": _workload(app, "web", is_public=True),
        "worker": _workload(app, "worker"),
        "nightly": _workload(app, "nightly", kind=Workload.Kind.CRONJOB.value, schedule="0 3 * * *"),
        "api": _workload(other, "api", is_public=True),
        "fn": _workload(other, "fn", kind=Workload.Kind.FUNCTION.value),
    }
    # bo made "fn" himself; everything else falls back to its app's creator.
    Workload.objects.filter(pk=rows["fn"].pk).update(created_by=people.bo)
    rival_org, _, rival_app = _scaffold(org_slug="rival", app_slug="hello-app")
    rival_app.created_by = people.ana
    rival_app.save(update_fields=["created_by"])
    _workload(rival_app, "web", is_public=True)
    _workload(rival_app, "nightly", kind=Workload.Kind.CRONJOB.value)
    return SimpleNamespace(org=org, app=app, other=other, rows=rows, rival_org=rival_org, rival_app=rival_app)


def _slugs(page):
    return [w.slug for w in page.items]


def test_numbered_page_sorts_and_counts_the_filtered_set(fleet):
    page = _page(fleet.org, sort="name", page=1, page_size=2)
    assert _slugs(page) == ["api", "fn"]
    assert (page.total_count, page.page, page.page_size, page.next_cursor) == (5, 1, 2, None)
    second = _page(fleet.org, sort="name", page=3, page_size=2)
    assert _slugs(second) == ["worker"]


def test_multi_key_sort(fleet):
    page = _page(fleet.org, sort="-public,app,name")
    assert _slugs(page) == ["web", "api", "nightly", "worker", "fn"]


def test_filter_kind_and_public_narrow_the_total(fleet):
    public = _page(fleet.org, page=1, filter=WorkloadsListFilterInput(is_public=True))
    assert sorted(_slugs(public)) == ["api", "web"]
    assert public.total_count == 2
    internal = _page(fleet.org, page=1, filter=WorkloadsListFilterInput(is_public=False, kind=["deployment"]))
    assert _slugs(internal) == ["worker"]
    assert internal.total_count == 1


def test_filter_applies_on_the_cursor_path_too(fleet):
    page = _page(fleet.org, limit=10, filter=WorkloadsListFilterInput(app=["other-app"]))
    assert sorted(_slugs(page)) == ["api", "fn"]
    assert page.total_count == 2
    assert page.page is None


def test_owner_is_the_workload_creator_else_the_apps(fleet, people):
    page = _page(fleet.org, people.ana, sort="name")
    owners = {w.slug: (w.owner_user_id, w.owned_by_me) for w in page.items}
    assert owners["web"] == (str(people.ana.pk), True)
    assert owners["fn"] == (str(people.bo.pk), False)
    assert owners["api"] == (None, False)


def test_owner_me_filter(fleet, people):
    mine = _page(fleet.org, people.ana, sort="name", filter=WorkloadsListFilterInput(owner=["me"]))
    assert _slugs(mine) == ["nightly", "web", "worker"]
    bos = _page(
        fleet.org, people.ana, sort="name", filter=WorkloadsListFilterInput(owner=[str(people.bo.pk)])
    )
    assert _slugs(bos) == ["fn"]
    nobody = _page(fleet.org, None, sort="name", filter=WorkloadsListFilterInput(owner=["me"]))
    assert nobody.total_count == 0


def test_owner_filter_never_reaches_another_org(fleet, people):
    """ana created apps in both orgs; her Mine view in one shows only that org's rows."""
    mine = _page(fleet.org, people.ana, sort="name", filter=WorkloadsListFilterInput(owner=["me"]))
    rival_ids = set(Workload.objects.filter(registered_app=fleet.rival_app).values_list("guid", flat=True))
    assert not {str(g) for g in rival_ids} & {str(w.id) for w in mine.items}
    assert mine.total_count == 3


def test_undeclared_sort_is_refused(fleet):
    with pytest.raises(UnsupportedSort):
        _page(fleet.org, sort="replicas")


@pytest.fixture
def env(fleet, provider_plugin_row):
    cluster = TenantCluster.objects.create(
        organization=fleet.org,
        name="c",
        slug="c",
        provider_plugin=provider_plugin_row,
        provider_config={},
        endpoint="https://c.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    return AppEnvironment.objects.create(registered_app=fleet.app, tenant_cluster=cluster, name="production")


@pytest.fixture
def provider_plugin_row():
    from astrolift_clusters.models import ProviderPlugin

    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="Test Provider",
                slug="test-provider",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    return ProviderPlugin.objects.get(slug="test-provider")


def test_cron_jobs_carry_their_latest_run(fleet, env):
    nightly = fleet.rows["nightly"]
    old = ScheduledJobRun.objects.create(workload=nightly, app_environment=env, status="succeeded")
    new = ScheduledJobRun.objects.create(
        workload=nightly, app_environment=env, status="failed", trigger_kind="manual", exit_code=2
    )
    ScheduledJobRun.objects.filter(pk=old.pk).update(created_at=timezone.now() - dt.timedelta(hours=1))

    page = _page(fleet.org, sort="name", filter=WorkloadsListFilterInput(kind=["cronjob"]))
    (job,) = page.items
    assert job.last_run is not None
    assert (str(job.last_run.id), job.last_run.status, job.last_run.trigger_kind, job.last_run.exit_code) == (
        str(new.guid),
        "failed",
        "manual",
        2,
    )
    others = _page(fleet.org, sort="name", filter=WorkloadsListFilterInput(kind=["deployment"]))
    assert all(w.last_run is None for w in others.items)

    with tenant_context(TenantContext(organization_id=fleet.org.id)):
        single = RegistryQuery().astrolift_workload(_info(), app_slug="hello-app", slug="nightly")
    assert str(single.last_run.id) == str(new.guid)


def test_last_run_is_one_query_per_page(fleet, env):
    ScheduledJobRun.objects.create(workload=fleet.rows["nightly"], app_environment=env)
    for n in range(4):
        job = _workload(fleet.app, f"job-{n}", kind=Workload.Kind.CRONJOB.value)
        ScheduledJobRun.objects.create(workload=job, app_environment=env)

    def run_queries(size):
        with CaptureQueriesContext(connection) as ctx:
            page = _page(
                fleet.org, sort="name", page_size=size, filter=WorkloadsListFilterInput(kind=["cronjob"])
            )
        assert all(w.last_run is not None for w in page.items)
        return [q for q in ctx.captured_queries if "astrolift_lifecycle_scheduledjobrun" in q["sql"]]

    assert len(run_queries(1)) == len(run_queries(5)) == 1
