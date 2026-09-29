"""The list contract on ``astroliftAppsPage`` / ``astroliftMyAppsPage`` (#2149).

The Apps screen used to walk every app, every workload and every environment
and answer its views, filters, sorts and page numbers in the browser. These
pin the server answering the same questions: numbered pages with an exact
count, the archived-only and failing views, status / deploy / kind / cluster /
project filters, and the four column sorts in both directions.

Built on the #481 scaffold: Alpha ok, Bravo failed, Charlie stale, Delta never
deployed (team A), Echo ok (team B), all on one cluster.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from astrolift_clusters.models import ManagedDomain
from astrolift_graphql import UnsupportedSort
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_registry.schema.queries import RegistryQuery
from astrolift_registry.schema.types import (
    AppListState,
    AppsListFilterInput,
    AppsListSortKey,
    AstroliftAppHealthPulseStatus,
    AstroliftAppListStatusFilter,
)
from astrolift_registry.tests.test_apps_list_filters import (  # noqa: F401
    _cluster,
    _info,
    _no_opensearch_profile_index,
    _provider_plugin,
    _run_in_tenant,
    _scaffold,
    _superuser,
)
from astrolift_registry.topology import classify_topology
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _page(scaffold, user, **kw):
    return _run_in_tenant(
        {"organization_id": scaffold.org.id, "actor_user_id": user.id},
        lambda: RegistryQuery().astrolift_apps_page(_info(), **kw),
    )


def _names(page) -> list[str]:
    return [row.name for row in page.items]


@pytest.fixture
def world():
    scaffold = _scaffold("-2149")
    apps = scaffold.apps
    RegisteredApp.objects.filter(pk=apps["ok-a"].pk).update(provisioning_status="ready")
    RegisteredApp.objects.filter(pk=apps["ok-b"].pk).update(provisioning_status="ready")
    RegisteredApp.objects.filter(pk=apps["never-a"].pk).update(provisioning_status="failed")
    Workload.objects.create(registered_app=apps["ok-a"], name="web", slug="web", kind="deployment")
    Workload.objects.create(registered_app=apps["ok-a"], name="worker", slug="worker", kind="deployment")
    Workload.objects.create(registered_app=apps["ok-b"], name="nightly", slug="nightly", kind="cronjob")
    other = _cluster(scaffold.org, _provider_plugin("-2149-b"), slug="-2149-b")
    AppEnvironment.objects.create(
        registered_app=apps["ok-b"], tenant_cluster=other, name="staging", url="", required_approvals=0
    )
    archived = RegisteredApp.objects.create(
        organization=scaffold.org,
        team=scaffold.team_a,
        project=scaffold.project_a,
        name="Foxtrot",
        slug="foxtrot-app-2149",
        source_kind="github",
    )
    archived.archived_at = archived.created_at
    archived.save(update_fields=["archived_at"])
    scaffold.other_cluster = other
    scaffold.user = _superuser("contract-2149")
    return scaffold


# ---------- numbered paging ---------------------------------------------


def test_numbered_pages_carry_an_exact_count_and_echo_the_page(world):
    first = _page(world, world.user, sort="name", page=1, page_size=2)
    second = _page(world, world.user, sort="name", page=2, page_size=2)
    third = _page(world, world.user, sort="name", page=3, page_size=2)

    assert _names(first) == ["Alpha", "Bravo"]
    assert _names(second) == ["Charlie", "Delta"]
    assert _names(third) == ["Echo"]
    assert (first.total_count, first.page, first.page_size, first.next_cursor) == (5, 1, 2, None)


def test_a_page_past_the_end_is_empty_but_keeps_the_count(world):
    page = _page(world, world.user, page=9, page_size=25)
    assert page.items == []
    assert page.total_count == 5
    assert page.page == 9


def test_without_page_or_sort_the_cursor_walk_is_unchanged(world):
    page = _page(world, world.user, limit=2)
    assert page.page is None
    assert page.next_cursor is not None
    assert page.total_count == 5


def test_a_numbered_page_costs_the_same_at_any_size(world):
    """Filters and sorts are annotations, not per-row work (the #481 N+1 contract)."""
    for i in range(6):
        RegisteredApp.objects.create(
            organization=world.org,
            team=world.team_a,
            project=world.project_a,
            name=f"Bulk {i}",
            slug=f"bulk-{i}-2149",
            source_kind="github",
        )
    kw = {"filter": AppsListFilterInput(failing=False, kind=["service-worker", "service"]), "sort": "-status"}
    with tenant_context(TenantContext(organization_id=world.org.id, actor_user_id=world.user.id)):
        with CaptureQueriesContext(connection) as small:
            RegistryQuery().astrolift_apps_page(_info(), page_size=1, include_freshness=True, **kw)
        with CaptureQueriesContext(connection) as full:
            RegistryQuery().astrolift_apps_page(_info(), page_size=100, include_freshness=True, **kw)
    assert len(small.captured_queries) == len(full.captured_queries)


# ---------- sort ------------------------------------------------------------


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("name", ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]),
        ("-name", ["Echo", "Delta", "Charlie", "Bravo", "Alpha"]),
        # Latest success, else latest deploy; never deployed sorts lowest.
        # Echo's deploy was written a moment after Alpha's, so it is newer.
        ("-deployed,name", ["Echo", "Alpha", "Bravo", "Charlie", "Delta"]),
        ("deployed,name", ["Delta", "Charlie", "Bravo", "Alpha", "Echo"]),
        # ready, then pending, then failed; ties by name.
        ("status,name", ["Alpha", "Echo", "Bravo", "Charlie", "Delta"]),
        ("-status,name", ["Delta", "Bravo", "Charlie", "Alpha", "Echo"]),
    ],
)
def test_every_declared_sort_in_both_directions(world, sort, expected):
    assert _names(_page(world, world.user, sort=sort)) == expected


def test_created_sorts_both_ways(world):
    newest = _names(_page(world, world.user, sort="-created"))
    oldest = _names(_page(world, world.user, sort="created"))
    assert newest == list(reversed(oldest))


def test_the_legacy_sort_by_still_orders_a_numbered_page(world):
    page = _page(world, world.user, sort_by=AppsListSortKey.NAME_ASC, page=1)
    assert _names(page) == ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]


def test_an_undeclared_sort_key_is_refused(world):
    with pytest.raises(UnsupportedSort):
        _page(world, world.user, sort="-owner")


# ---------- filters ---------------------------------------------------------


def test_archived_only_holds_only_archived_apps(world):
    page = _page(world, world.user, filter=AppsListFilterInput(archived=True), page=1)
    assert _names(page) == ["Foxtrot"]


def test_include_archived_still_mixes_both(world):
    page = _page(world, world.user, include_archived=True, page=1, sort="name")
    assert "Foxtrot" in _names(page)
    assert page.total_count == 6


def test_failing_means_provisioning_failed_or_latest_deploy_failed(world):
    failing = _page(world, world.user, filter=AppsListFilterInput(failing=True), sort="name")
    healthy = _page(world, world.user, filter=AppsListFilterInput(failing=False), sort="name")
    assert _names(failing) == ["Bravo", "Delta"]
    assert _names(healthy) == ["Alpha", "Charlie", "Echo"]


def test_status_filter_on_the_header_status(world):
    page = _page(
        world,
        world.user,
        filter=AppsListFilterInput(status=[AppListState.READY, AppListState.FAILED]),
        sort="name",
    )
    assert _names(page) == ["Alpha", "Delta", "Echo"]


def test_ready_with_a_managed_zone_is_live(world):
    domain = ManagedDomain.objects.create(
        organization=world.org, zone="apps-2149.example", dns_driver="route53", default_for="tenant_apps"
    )
    world.org.default_managed_domain = domain
    world.org.save(update_fields=["default_managed_domain"])

    live = _page(world, world.user, filter=AppsListFilterInput(status=[AppListState.LIVE]), sort="name")
    ready = _page(world, world.user, filter=AppsListFilterInput(status=[AppListState.READY]), sort="name")
    assert _names(live) == ["Alpha", "Echo"]
    assert _names(ready) == []


def test_deploy_filter_matches_the_row_health_pulse(world):
    page = _page(
        world,
        world.user,
        filter=AppsListFilterInput(deploy=[AstroliftAppHealthPulseStatus.STALE]),
        include_freshness=True,
        page=1,
    )
    assert _names(page) == ["Charlie"]
    assert page.items[0].health_pulse.status is AstroliftAppHealthPulseStatus.STALE


def test_the_legacy_status_argument_filters_a_numbered_page_in_sql(world):
    page = _page(world, world.user, status=AstroliftAppListStatusFilter.NEVER_DEPLOYED, page=1)
    assert _names(page) == ["Delta"]
    assert page.total_count == 1


def test_kind_filter_classifies_from_workloads(world):
    worker = _page(world, world.user, filter=AppsListFilterInput(kind=["service-worker"]), page=1)
    scheduled = _page(world, world.user, filter=AppsListFilterInput(kind=["scheduled", "service"]), page=1)
    assert _names(worker) == ["Alpha"]
    assert _names(scheduled) == ["Echo"]


def test_cluster_filter_matches_any_environment_case_insensitive(world):
    slug = world.other_cluster.slug.upper()
    page = _page(world, world.user, filter=AppsListFilterInput(cluster=[slug]), page=1)
    assert _names(page) == ["Echo"]


def test_project_filter_matches_slug_or_name(world):
    by_name = _page(world, world.user, filter=AppsListFilterInput(project=["infra"]), page=1)
    by_slug = _page(world, world.user, filter=AppsListFilterInput(project=[world.project_b.slug]), page=1)
    assert _names(by_name) == _names(by_slug) == ["Echo"]


def test_filters_combine_and_apply_on_the_cursor_path_too(world):
    page = _page(
        world,
        world.user,
        filter=AppsListFilterInput(failing=True, team=[world.team_a.slug], status=[AppListState.PENDING]),
    )
    assert page.page is None
    assert _names(page) == ["Bravo"]
    assert page.total_count == 1


def test_my_apps_page_takes_the_same_contract(world):
    page = _run_in_tenant(
        {"organization_id": world.org.id, "actor_user_id": world.user.id},
        lambda: RegistryQuery().astrolift_my_apps_page(
            _info(), filter=AppsListFilterInput(failing=True), sort="-name", page=1, page_size=1
        ),
    )
    assert _names(page) == ["Delta"]
    assert page.total_count == 2


# ---------- search ----------------------------------------------------------


def test_search_matches_a_guid_prefix(world):
    guid = str(world.apps["stale-a"].guid)
    page = _page(world, world.user, search=guid[:28], page=1)
    assert _names(page) == ["Charlie"]


# ---------- row fields ------------------------------------------------------


def test_rows_carry_topology_kind_and_cluster_slugs(world):
    rows = {row.name: row for row in _page(world, world.user, sort="name").items}
    assert rows["Alpha"].topology_kind == "service-worker"
    assert rows["Echo"].topology_kind == "scheduled"
    assert rows["Delta"].topology_kind is None
    assert rows["Echo"].cluster_slugs == [world.cluster.slug, world.other_cluster.slug]
    assert rows["Alpha"].cluster_slugs == [world.cluster.slug]


# ---------- topology parity with frontend/lib/topology.test.ts --------------


@pytest.mark.parametrize(
    ("workloads", "services", "expected"),
    [
        ([("deployment", "web")], [], "service"),
        ([("deployment", "web")], ["postgres"], "service-data"),
        ([("deployment", "api"), ("statefulset", "postgres")], [], "service-data"),
        ([("deployment", "web")], ["email"], "service"),
        ([("deployment", "web"), ("deployment", "worker")], [], "service-worker"),
        ([("deployment", "web"), ("deployment", "mailer")], ["sqs"], "service-worker"),
        ([("deployment", "web"), ("deployment", "billing"), ("deployment", "search")], [], "microservices"),
        ([("deployment", "web"), ("deployment", "api")], [], "microservices"),
        ([("deployment", "web"), ("agent", "triage")], [], "service-agent"),
        ([("agent", "a"), ("agent", "b")], [], "agent"),
        ([("agent", "a"), ("cronjob", "sweep")], [], "mixed"),
        ([("function", "resize"), ("function", "thumb")], ["s3"], "functions"),
        ([("cronjob", "nightly")], [], "scheduled"),
        ([("job", "migrate"), ("task", "seed")], [], "task"),
        ([("workflow", "release")], [], "workflow"),
        ([("deployment", "web"), ("function", "hook")], [], "mixed"),
        ([], [], "service"),
    ],
)
def test_classify_topology_matches_the_frontend(workloads, services, expected):
    assert classify_topology(workloads, services) == expected
