"""The Clusters list contract and the single-cluster read (#2150).

The detail page and every cluster tab found their cluster in the
deprecated ``astroliftClusters`` list, which stops at 200 rows sorted by
slug, so the 201st cluster read as "not found" on its own page.
``astroliftCluster(slug)`` reads one row; the 250-cluster test below is
the regression.

``astroliftClustersPage`` gains the list contract (spec 44 §5.1): the
filters, sorts and numbered pages /clusters used to answer in the
browser over the whole fleet.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_clusters.heartbeat_status import resolve as resolve_heartbeat
from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.mutations import ClustersMutation, RegisterTenantClusterInput
from astrolift_clusters.schema.queries import ClustersQuery, _annotate_clusters_list, _clusters_qs
from astrolift_clusters.schema.types import ClustersListFilterInput
from astrolift_clusters.tests.test_heartbeat import (  # noqa: F401 (fixture)
    _no_opensearch_profile_index,
)
from astrolift_graphql import UnsupportedSort
from astrolift_identity.models import Organization
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


def _info(user=None):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=SimpleNamespace(user=user)))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _org(tag: str) -> Organization:
    return Organization.objects.create(name=tag, slug=f"{tag}-{uuid.uuid4().hex[:6]}")


def _plugin(slug: str) -> ProviderPlugin:
    [p] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name=slug, slug=f"{slug}-{uuid.uuid4().hex[:4]}", capabilities_manifest={}, config_schema={}
            )
        ]
    )
    return p


def _cluster(org, plugin, slug: str, **kwargs) -> TenantCluster:
    return TenantCluster.objects.create(
        organization=org,
        name=kwargs.pop("name", slug),
        slug=slug,
        provider_plugin=plugin,
        provider_config={},
        endpoint=f"https://{slug}.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        **kwargs,
    )


def _bulk(org, plugin, slugs) -> None:
    TenantCluster.objects.bulk_create(
        [
            TenantCluster(
                organization=org,
                name=slug,
                slug=slug,
                provider_plugin=plugin,
                provider_config={},
                endpoint=f"https://{slug}.invalid",
                auth_method=TenantCluster.AuthMethod.KUBECONFIG,
                auth_config={},
            )
            for slug in slugs
        ]
    )


def _slugs(page) -> list[str]:
    return [c.slug for c in page.items]


# ---------------------------------------------------------------------------
# astroliftCluster(slug): the detail read
# ---------------------------------------------------------------------------


def test_the_201st_cluster_resolves_on_its_own_detail_page(permission_resolver):
    """The bug: detail found the cluster in a list that stops at 200."""
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    org = _org("fleet")
    plugin = _plugin("fleet")
    slugs = [f"c{n:03d}" for n in range(250)]
    _bulk(org, plugin, slugs)

    query = ClustersQuery()
    with _ctx(org):
        capped = [c.slug for c in query.astrolift_clusters(_info())]
        assert len(capped) == 200 and "c200" not in capped, "precondition: the list field still caps"

        for slug in ("c000", "c200", "c249"):
            cluster = query.astrolift_cluster(_info(), slug=slug)
            assert cluster is not None, slug
            assert cluster.slug == slug

        # ...and the numbered list reaches it too: page 9 of 25 holds 200-224.
        page = query.astrolift_clusters_page(_info(), sort="slug", page=9, page_size=25)
    assert page.total_count == 250
    assert _slugs(page)[0] == "c200"
    assert page.page == 9 and page.page_size == 25 and page.next_cursor is None


def test_detail_read_keeps_the_list_visibility(permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    org = _org("vis")
    other = _org("other")
    plugin = _plugin("vis")
    _cluster(org, plugin, "vis-ours")
    _cluster(None, plugin, "vis-shared")
    _cluster(other, plugin, "vis-theirs")
    _cluster(org, plugin, "vis-gone").soft_delete()

    query = ClustersQuery()
    with _ctx(org):
        assert query.astrolift_cluster(_info(), slug="vis-ours").slug == "vis-ours"
        assert query.astrolift_cluster(_info(), slug="vis-shared").slug == "vis-shared"
        assert query.astrolift_cluster(_info(), slug="vis-theirs") is None, "another org's cluster leaked"
        assert query.astrolift_cluster(_info(), slug="vis-gone") is None
        assert query.astrolift_cluster(_info(), slug="nope") is None


def test_detail_read_refuses_without_a_tenant(permission_resolver):
    from core.decorators import TenantRequired

    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    _cluster(None, _plugin("nt"), "nt-shared")
    with tenant_context(TenantContext(organization_id=None)):
        with pytest.raises(TenantRequired):
            ClustersQuery().astrolift_cluster(_info(), slug="nt-shared")


# ---------------------------------------------------------------------------
# astroliftClustersPage: filters
# ---------------------------------------------------------------------------


def test_filters_provider_status_and_registered_by(permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    org = _org("flt")
    aws = _plugin("aws")
    k8s = _plugin("k8s")
    ana = User.objects.create(username=f"ana-{uuid.uuid4().hex[:6]}")
    bo = User.objects.create(username=f"bo-{uuid.uuid4().hex[:6]}")
    _cluster(org, aws, "flt-a", lifecycle="managed", created_by=ana)
    _cluster(org, aws, "flt-b", lifecycle="error", created_by=bo)
    _cluster(org, k8s, "flt-c", lifecycle="managed", created_by=ana)
    _cluster(org, k8s, "flt-d", lifecycle="registered")

    query = ClustersQuery()

    def listed(user=None, **filters):
        with _ctx(org):
            page = query.astrolift_clusters_page(
                _info(user), filter=ClustersListFilterInput(**filters), sort="slug", page=1
            )
        assert page.total_count == len(page.items)
        return _slugs(page)

    assert listed(provider=[aws.slug]) == ["flt-a", "flt-b"]
    assert listed(status=["managed"]) == ["flt-a", "flt-c"]
    assert listed(status=["managed", "error"], provider=[k8s.slug]) == ["flt-c"]
    assert listed(ana, registered_by=["me"]) == ["flt-a", "flt-c"]
    assert listed(ana, registered_by=[bo.username]) == ["flt-b"]
    assert listed(None, registered_by=["me"]) == [], "no viewer must match nothing, not everything"
    assert listed(provider=[]) == ["flt-a", "flt-b", "flt-c", "flt-d"], "an empty list does not filter"


def test_live_filter_matches_the_heartbeat_status_the_row_shows(permission_resolver):
    """The Offline view counts exactly the rows the Live column paints red."""
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    org = _org("live")
    plugin = _plugin("live")
    now = timezone.now()
    ages = {"hb-never": None, "hb-fresh": 10, "hb-late": 60, "hb-dead": 200, "hb-edge": 90}
    for slug, age in ages.items():
        _cluster(
            org,
            plugin,
            slug,
            heartbeat_interval_seconds=30,
            last_heartbeat_at=None if age is None else now - dt.timedelta(seconds=age),
        )
    # A zero interval floors at MIN_INTERVAL_SECONDS in Python; SQL has to agree.
    _cluster(
        org, plugin, "hb-zero", heartbeat_interval_seconds=0, last_heartbeat_at=now - dt.timedelta(seconds=6)
    )

    with _ctx(org):
        annotated = {c.slug: c._heartbeat_status for c in _annotate_clusters_list(_clusters_qs(), now=now)}
        expected = {
            c.slug: resolve_heartbeat(
                last_heartbeat_at=c.last_heartbeat_at, interval_seconds=c.heartbeat_interval_seconds, now=now
            ).value
            for c in _clusters_qs()
        }
        offline = ClustersQuery().astrolift_clusters_page(
            _info(), filter=ClustersListFilterInput(live=["offline"]), sort="slug", page=1
        )
    assert annotated == expected
    assert expected["hb-dead"] == "offline" and expected["hb-edge"] == "offline"
    assert _slugs(offline) == ["hb-dead", "hb-edge"]


def test_filter_applies_on_the_cursor_walk_too(permission_resolver):
    """No sort, page or pageSize: the old cursor walk, filtered first."""
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    org = _org("cur")
    plugin = _plugin("cur")
    for n in range(6):
        _cluster(org, plugin, f"cur-{n}", lifecycle="managed" if n % 2 else "registered")

    slugs: list[str] = []
    cursor = None
    with _ctx(org):
        for _ in range(10):
            page = ClustersQuery().astrolift_clusters_page(
                _info(), limit=1, after=cursor, filter=ClustersListFilterInput(status=["managed"])
            )
            assert page.page is None and page.total_count == 3
            slugs.extend(_slugs(page))
            cursor = page.next_cursor
            if cursor is None:
                break
    assert slugs == ["cur-1", "cur-3", "cur-5"]


# ---------------------------------------------------------------------------
# astroliftClustersPage: sort and numbered pages
# ---------------------------------------------------------------------------


def test_sorts(permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    org = _org("srt")
    plugin = _plugin("srt")
    now = timezone.now()
    _cluster(org, plugin, "srt-a", name="Bravo", lifecycle="error", capabilities_probed_at=now)
    _cluster(org, plugin, "srt-b", name="alpha", lifecycle="managed")
    _cluster(
        org,
        plugin,
        "srt-c",
        name="Charlie",
        lifecycle="registered",
        capabilities_probed_at=now - dt.timedelta(1),
    )

    def order(sort):
        with _ctx(org):
            return _slugs(ClustersQuery().astrolift_clusters_page(_info(), sort=sort))

    assert order("name") == ["srt-b", "srt-a", "srt-c"], "name sorts case-insensitively"
    assert order("-name") == ["srt-c", "srt-a", "srt-b"]
    assert order("status") == ["srt-c", "srt-b", "srt-a"], "registered, managed, error: the lifecycle order"
    assert order("-lastProbe,name") == ["srt-a", "srt-c", "srt-b"], "never probed sorts last descending"
    assert order("lastProbe") == ["srt-b", "srt-c", "srt-a"]
    with _ctx(org), pytest.raises(UnsupportedSort):
        ClustersQuery().astrolift_clusters_page(_info(), sort="endpoint")


def test_numbered_page_defaults_and_past_the_end(permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    org = _org("num")
    plugin = _plugin("num")
    _bulk(org, plugin, [f"num-{n:02d}" for n in range(30)])

    with _ctx(org):
        first = ClustersQuery().astrolift_clusters_page(_info(), page=1)
        second = ClustersQuery().astrolift_clusters_page(_info(), page=2)
        past = ClustersQuery().astrolift_clusters_page(_info(), page=9)
    assert (first.page, first.page_size, first.total_count) == (1, 25, 30)
    assert len(first.items) == 25 and len(second.items) == 5
    assert set(_slugs(first)).isdisjoint(_slugs(second))
    assert past.items == [] and past.total_count == 30


# ---------------------------------------------------------------------------
# Registered by
# ---------------------------------------------------------------------------


def test_register_records_who_and_the_row_reports_it(permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    org = _org("reg")
    plugin = _plugin("reg")
    user = User.objects.create(username=f"reg-{uuid.uuid4().hex[:6]}")
    slug = f"reg-{uuid.uuid4().hex[:6]}"

    with _ctx(org):
        result = ClustersMutation().register_tenant_cluster(
            _info(user),
            RegisterTenantClusterInput(
                slug=slug,
                name="Registered",
                provider_plugin_slug=plugin.slug,
                auth_method="kubeconfig",
                organization_scoped=True,
            ),
        )
        assert result.ok, result
        cluster = ClustersQuery().astrolift_cluster(_info(user), slug=slug)
    assert TenantCluster.objects.get(slug=slug).created_by_id == user.pk
    assert cluster.created_by_username == user.username


def test_a_shared_cluster_does_not_report_its_registrant(permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    org = _org("shr")
    operator = User.objects.create(username=f"op-{uuid.uuid4().hex[:6]}")
    _cluster(None, _plugin("shr"), "shr-platform", created_by=operator)
    with _ctx(org):
        cluster = ClustersQuery().astrolift_cluster(_info(), slug="shr-platform")
    assert cluster.created_by_username is None
