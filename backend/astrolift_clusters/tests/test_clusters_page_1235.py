"""astroliftClustersPage — cursor pagination over the cluster inventory (#1235).

``astroliftClusters`` slices at 200 rows and offers no way to reach the
201st: an install that manages a cluster per region per environment walks
off that cliff, and /clusters simply stops listing the rest. That is the
operator-visible bug behind #1230, and these tests are what stop it
coming back.

Two properties beyond "does it paginate" get pinned here because the
cluster surface is where they bite:

* the walk is ALPHABETICAL by slug — the ordering the deprecated list
  field serves — so switching /clusters over doesn't reshuffle the table;
* platform-level clusters (``organization`` null) stay visible to every
  org while another org's rows appear in neither the page nor the count.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.queries import ClustersQuery, _clusters_qs
from astrolift_identity.models import Organization
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _plugin(slug: str = "local") -> ProviderPlugin:
    """Skip BaseCoreModel.save (numeric version vs. ProviderPlugin's
    CharField override) — same trick as the rest of this suite."""
    [p] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name=slug,
                slug=slug,
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    return p


def _cluster(org, plugin, slug: str, **kwargs) -> TenantCluster:
    """A registered cluster. ``org=None`` makes it platform-level.

    Cluster slugs are globally unique across live rows (partial index
    ``tenant_cluster_slug_unique_active``), not unique per org — every
    caller here has to pick a distinct slug even across orgs.
    """
    return TenantCluster.objects.create(
        organization=org,
        name=kwargs.pop("name", slug),
        slug=slug,
        provider_plugin=plugin,
        provider_config={},
        endpoint=kwargs.pop("endpoint", f"https://{slug}.invalid"),
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        **kwargs,
    )


def _walk(query, org, *, limit, **kwargs) -> list[str]:
    """Page through the whole inventory, returning every slug in order."""
    slugs: list[str] = []
    cursor: str | None = None
    with _ctx(org):
        for _ in range(20):  # bounded so a non-terminating walk fails loudly
            page = query.astrolift_clusters_page(_info(), limit=limit, after=cursor, **kwargs)
            slugs.extend(item.slug for item in page.items)
            cursor = page.next_cursor
            if cursor is None:
                return slugs
    raise AssertionError("walk did not terminate")


def test_page_reaches_past_the_old_two_hundred_row_cap(permission_resolver):
    """The regression that motivated the epic: with the list field, the
    201st cluster did not exist as far as /clusters was concerned."""
    org = Organization.objects.create(name="Acme", slug="acme-cap")
    plugin = _plugin("cap")
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    for n in range(205):
        _cluster(org, plugin, f"c{n:03d}")

    query = ClustersQuery()
    with _ctx(org):
        capped = query.astrolift_clusters(_info())
    assert len(capped) == 200, "precondition: the list field still caps"

    slugs = _walk(query, org, limit=50)
    assert len(slugs) == 205
    assert len(set(slugs)) == 205, "a row was served twice"
    assert set(slugs) == {f"c{n:03d}" for n in range(205)}


def test_walk_is_alphabetical_and_loses_nothing(permission_resolver):
    """The seek key is ``(slug, guid)`` ascending, matching the ordering
    the deprecated list field serves."""
    org = Organization.objects.create(name="Acme", slug="acme-order")
    plugin = _plugin("order")
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    for slug in ("zulu", "alpha", "mike", "bravo", "yankee", "charlie", "delta"):
        _cluster(org, plugin, slug)

    expected = list(
        TenantCluster.objects.filter(organization=org).order_by("slug", "guid").values_list("slug", flat=True)
    )
    assert _walk(ClustersQuery(), org, limit=3) == expected


def test_page_and_deprecated_list_field_agree(permission_resolver):
    """Both fields build on one queryset helper so their notion of "a
    cluster" cannot drift; this pins that they really do."""
    org = Organization.objects.create(name="Acme", slug="acme-agree")
    plugin = _plugin("agree")
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    for slug in ("one", "two", "three", "four"):
        _cluster(org, plugin, slug)
    _cluster(None, plugin, "shared-platform")

    query = ClustersQuery()
    with _ctx(org):
        listed = [c.slug for c in query.astrolift_clusters(_info())]
    assert _walk(query, org, limit=2) == listed


def test_total_count_is_the_whole_result_set(permission_resolver):
    org = Organization.objects.create(name="Acme", slug="acme-total")
    plugin = _plugin("total")
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    for n in range(12):
        _cluster(org, plugin, f"t{n:02d}")

    with _ctx(org):
        page = ClustersQuery().astrolift_clusters_page(_info(), limit=5)
    assert len(page.items) == 5
    assert page.total_count == 12
    assert page.next_cursor is not None


def test_platform_clusters_stay_visible_to_every_org(permission_resolver):
    """``organization`` null means install-wide: the union the list field
    applies has to survive the conversion or shared clusters vanish."""
    org = Organization.objects.create(name="Acme", slug="acme-platform")
    plugin = _plugin("platform")
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    _cluster(org, plugin, "org-owned")
    _cluster(None, plugin, "install-wide")

    with _ctx(org):
        page = ClustersQuery().astrolift_clusters_page(_info(), limit=50)
    assert [c.slug for c in page.items] == ["install-wide", "org-owned"]
    assert page.total_count == 2


def test_other_orgs_clusters_are_invisible(permission_resolver):
    """TenantCluster's manager is not tenant-aware, so the org union is
    the resolver's job (#1183) — in the items AND in the count."""
    org = Organization.objects.create(name="Acme", slug="acme-iso")
    other = Organization.objects.create(name="Other", slug="other-iso")
    plugin = _plugin("iso")
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    _cluster(org, plugin, "ours")
    _cluster(other, plugin, "theirs")

    with _ctx(org):
        page = ClustersQuery().astrolift_clusters_page(_info(), limit=50)
    assert [c.slug for c in page.items] == ["ours"]
    assert page.total_count == 1, "the count leaked the other org's row"

    # …and the walk can't reach it either: a full walk is the only way to
    # prove the foreign row isn't merely off the first page.
    assert _walk(ClustersQuery(), org, limit=1) == ["ours"]


def test_search_matches_name_endpoint_region_and_provider_slug(permission_resolver):
    org = Organization.objects.create(name="Acme", slug="acme-search")
    aws = _plugin("aws")
    k8s = _plugin("k8s-native")
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    _cluster(org, aws, "prod-east", name="Production East", region="us-east-1")
    _cluster(org, k8s, "lab-metal", name="Lab", region="rack-7", endpoint="https://metal.lan")
    _cluster(org, aws, "stage-west", name="Staging West", region="us-west-2")

    query = ClustersQuery()
    with _ctx(org):
        by_name = query.astrolift_clusters_page(_info(), search="Staging")
        by_slug = query.astrolift_clusters_page(_info(), search="lab-")
        by_endpoint = query.astrolift_clusters_page(_info(), search="metal.lan")
        by_region = query.astrolift_clusters_page(_info(), search="us-east")
        by_provider = query.astrolift_clusters_page(_info(), search="k8s-native")

    assert [c.slug for c in by_name.items] == ["stage-west"]
    assert [c.slug for c in by_slug.items] == ["lab-metal"]
    assert [c.slug for c in by_endpoint.items] == ["lab-metal"]
    assert [c.slug for c in by_region.items] == ["prod-east"]
    assert [c.slug for c in by_provider.items] == ["lab-metal"]


def test_search_narrows_total_count_not_just_the_page(permission_resolver):
    """A count that ignored the search would render "9 results" over a
    one-row table."""
    org = Organization.objects.create(name="Acme", slug="acme-narrow")
    plugin = _plugin("narrow")
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    _cluster(org, plugin, "keep-me")
    for n in range(8):
        _cluster(org, plugin, f"other-{n}")

    query = ClustersQuery()
    with _ctx(org):
        unfiltered = query.astrolift_clusters_page(_info(), limit=2)
        narrowed = query.astrolift_clusters_page(_info(), limit=2, search="keep-me")
    assert unfiltered.total_count == 9
    assert narrowed.total_count == 1
    assert [c.slug for c in narrowed.items] == ["keep-me"]
    assert narrowed.next_cursor is None

    # The search has to survive the cursor too — a walk that dropped it on
    # page two would serve the other eight rows.
    assert _walk(query, org, limit=1, search="other-") == [f"other-{n}" for n in range(8)]


def test_soft_deleted_clusters_are_absent(permission_resolver):
    """The default manager hides them, which is also what makes the slug
    seek key unique across the walk (the unique index is partial on live
    rows) — so this is a correctness precondition, not just a filter."""
    org = Organization.objects.create(name="Acme", slug="acme-deleted")
    plugin = _plugin("deleted")
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    _cluster(org, plugin, "alive")
    doomed = _cluster(org, plugin, "gone")
    doomed.soft_delete()

    with _ctx(org):
        page = ClustersQuery().astrolift_clusters_page(_info(), limit=50)
    assert [c.slug for c in page.items] == ["alive"]
    assert page.total_count == 1


def test_limit_is_clamped_to_the_shared_ceiling(permission_resolver):
    """A caller asking for 10_000 rows gets MAX_PAGE_LIMIT and a cursor,
    not the whole table in one response."""
    from astrolift_graphql import MAX_PAGE_LIMIT

    org = Organization.objects.create(name="Acme", slug="acme-clamp")
    plugin = _plugin("clamp")
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    for n in range(MAX_PAGE_LIMIT + 5):
        _cluster(org, plugin, f"k{n:04d}")

    with _ctx(org):
        page = ClustersQuery().astrolift_clusters_page(_info(), limit=10_000)
    assert len(page.items) == MAX_PAGE_LIMIT
    assert page.total_count == MAX_PAGE_LIMIT + 5
    assert page.next_cursor is not None


def test_no_tenant_context_is_refused_outright(permission_resolver):
    """Fails closed rather than returning every install's clusters.

    ``@tenant_scoped`` rejects first; ``_clusters_qs``'s ``org_id is
    None`` branch is defence in depth behind it — without that branch the
    ``Q(organization_id=None) | Q(organization_id__isnull=True)`` union
    would degrade to "every platform-level cluster" instead of nothing.
    """
    from core.decorators import TenantRequired

    org = Organization.objects.create(name="Acme", slug="acme-notenant")
    plugin = _plugin("notenant")
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    _cluster(org, plugin, "org-owned")
    _cluster(None, plugin, "install-wide")

    with tenant_context(TenantContext(organization_id=None)):
        with pytest.raises(TenantRequired):
            ClustersQuery().astrolift_clusters_page(_info(), limit=50)
        assert _clusters_qs().count() == 0
