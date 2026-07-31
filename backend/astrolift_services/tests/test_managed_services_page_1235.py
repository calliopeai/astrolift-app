"""astroliftManagedServicesPage — cursor pagination over an app's managed
services (#1235).

``astroliftManagedServices`` is worse than the capped list resolvers the
epic targets: it is *unbounded* AND it applies no ``order_by`` at all, so
one response carries every binding the app has ever had in whatever order
Postgres felt like returning them. The page field walks the same rows on a
``(-created_at, -guid)`` seek key.

Both fields are built from one queryset builder (``_managed_services_qs``),
so the org scope, the soft-delete filter, and the environment filter cannot
drift between them — several tests here assert exactly that.

Real Postgres, no DB mocks; resolvers are invoked directly inside a bound
tenant context (the services-test convention, see
``test_tenant_isolation_1183.py``).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService
from astrolift_services.schema.queries import ServicesQuery, _managed_services_qs
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Scaffolding
# ---------------------------------------------------------------------------


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _graph(suffix: str, *, app_slug: str | None = None) -> SimpleNamespace:
    """A full single-org graph: org → team → project → app → production env.

    ``app_slug`` is overridable so two orgs can carry an app with the SAME
    slug (``RegisteredApp.slug`` is unique per org, not globally) — which is
    what makes the cross-org test bite: the resolver's only non-org filter
    is the slug.
    """
    org = Organization.objects.create(name=f"Org {suffix}", slug=f"org-{suffix}")
    team = Team.objects.create(organization=org, name=f"Team {suffix}", slug=f"team-{suffix}")
    project = Project.objects.create(
        organization=org, team=team, name=f"Proj {suffix}", slug=f"proj-{suffix}"
    )
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="AWS",
                slug="aws",
                version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    plugin = ProviderPlugin.objects.get(slug="aws")
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"cluster-{suffix}",
        name=f"Cluster {suffix}",
        provider_plugin=plugin,
        endpoint="http://localhost:8443",
        region="us-east-1",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=f"App {suffix}",
        slug=app_slug or f"app-{suffix}",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(registered_app=app, name="production", tenant_cluster=cluster)
    return SimpleNamespace(org=org, team=team, project=project, cluster=cluster, app=app, env=env)


def _extra_env(graph: SimpleNamespace, name: str) -> AppEnvironment:
    return AppEnvironment.objects.create(registered_app=graph.app, name=name, tenant_cluster=graph.cluster)


def _svc(
    graph: SimpleNamespace,
    name: str,
    *,
    env: AppEnvironment | None = None,
    kind: str = ManagedService.Kind.POSTGRES,
    variant: str = "rds",
    status: str = ManagedService.Status.ACTIVE,
) -> ManagedService:
    return ManagedService.objects.create(
        registered_app=graph.app,
        app_environment=env or graph.env,
        kind=kind,
        name=name,
        variant=variant,
        status=status,
        config={},
    )


def _page(graph: SimpleNamespace, **kwargs):
    with tenant_context(TenantContext(organization_id=graph.org.id)):
        return ServicesQuery().astrolift_managed_services_page(_info(), app_slug=graph.app.slug, **kwargs)


def _walk(graph: SimpleNamespace, *, limit: int, **kwargs) -> list[str]:
    """Page through the whole stream, returning every service name in order."""
    names: list[str] = []
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=graph.org.id)):
        for _ in range(50):  # bounded so a non-terminating walk fails loudly
            page = ServicesQuery().astrolift_managed_services_page(
                _info(), app_slug=graph.app.slug, limit=limit, after=cursor, **kwargs
            )
            names.extend(item.name for item in page.items)
            cursor = page.next_cursor
            if cursor is None:
                return names
    raise AssertionError("walk did not terminate")


def _db_order(graph: SimpleNamespace, **filters) -> list[str]:
    """The seek key's own ordering, straight from Postgres."""
    return list(
        ManagedService.objects.filter(registered_app=graph.app, **filters)
        .order_by("-created_at", "-guid")
        .values_list("name", flat=True)
    )


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------


def test_walk_covers_every_row_exactly_once_and_terminates(permission_resolver):
    """The whole point: every binding is reachable, exactly once, in the
    seek key's order. Rows written in a tight loop share a ``created_at``,
    so this exercises the ``-guid`` tiebreak rather than dodging it."""
    permission_resolver.grant(Permission.APP_READ)
    a = _graph("a")
    for n in range(137):
        _svc(a, f"svc-{n:03d}")

    names = _walk(a, limit=25)

    assert len(names) == 137
    assert len(set(names)) == 137, "a row was served twice"
    assert names == _db_order(a)


def test_walk_terminates_when_the_last_page_is_exactly_full(permission_resolver):
    """End-of-stream comes from the overfetched row, not from
    ``len(items) < limit`` — a final page that lands exactly on the limit
    must still report ``nextCursor: null`` or the client loops forever."""
    permission_resolver.grant(Permission.APP_READ)
    a = _graph("a")
    for n in range(10):
        _svc(a, f"svc-{n}")

    first = _page(a, limit=5)
    assert len(first.items) == 5
    assert first.next_cursor is not None

    second = _page(a, limit=5, after=first.next_cursor)
    assert len(second.items) == 5
    assert second.next_cursor is None


def test_garbage_cursor_restarts_instead_of_erroring(permission_resolver):
    """A bookmarked / truncated token is a UX event, not a 400."""
    permission_resolver.grant(Permission.APP_READ)
    a = _graph("a")
    for n in range(4):
        _svc(a, f"svc-{n}")

    page = _page(a, limit=10, after="not-a-real-cursor")
    assert [i.name for i in page.items] == _db_order(a)


# ---------------------------------------------------------------------------
# total_count
# ---------------------------------------------------------------------------


def test_total_count_is_the_whole_result_set_not_the_page(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    a = _graph("a")
    for n in range(12):
        _svc(a, f"svc-{n}")

    page = _page(a, limit=5)

    assert len(page.items) == 5
    assert page.total_count == 12


# ---------------------------------------------------------------------------
# Filters + search
# ---------------------------------------------------------------------------


def test_environment_filter_narrows_items_and_total_count(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    a = _graph("a")
    staging = _extra_env(a, "staging")
    _svc(a, "prod-db")
    _svc(a, "prod-cache", kind=ManagedService.Kind.REDIS, variant="elasticache")
    _svc(a, "staging-db", env=staging)

    page = _page(a, environment_name="staging")

    assert [i.name for i in page.items] == ["staging-db"]
    assert page.total_count == 1


def test_search_matches_name_kind_variant_status_and_environment(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    a = _graph("a")
    staging = _extra_env(a, "staging")
    _svc(a, "primary-db")
    _svc(a, "cache", kind=ManagedService.Kind.REDIS, variant="elasticache")
    _svc(a, "uploads", kind=ManagedService.Kind.OBJECT_STORE, variant="s3")
    _svc(a, "broken", kind=ManagedService.Kind.QUEUE, variant="sqs", status=ManagedService.Status.FAILED)
    _svc(a, "worker-inbox", kind=ManagedService.Kind.QUEUE, variant="sqs", env=staging)

    assert [i.name for i in _page(a, search="uploads").items] == ["uploads"]
    assert [i.name for i in _page(a, search="redis").items] == ["cache"]
    assert [i.name for i in _page(a, search="elasticache").items] == ["cache"]
    assert [i.name for i in _page(a, search="failed").items] == ["broken"]
    assert [i.name for i in _page(a, search="staging").items] == ["worker-inbox"]


def test_search_narrows_total_count_not_just_the_page(permission_resolver):
    """A count that ignored the search would render "9 results" over a
    one-row table."""
    permission_resolver.grant(Permission.APP_READ)
    a = _graph("a")
    _svc(a, "keep-me")
    for n in range(8):
        _svc(a, f"other-{n}", kind=ManagedService.Kind.REDIS, variant="elasticache")

    page = _page(a, search="keep-me")

    assert [i.name for i in page.items] == ["keep-me"]
    assert page.total_count == 1


def test_search_walk_is_still_complete(permission_resolver):
    """Search + cursor compose: the seek clause must not drop matches once
    the filter is in play."""
    permission_resolver.grant(Permission.APP_READ)
    a = _graph("a")
    for n in range(40):
        _svc(a, f"keep-{n:02d}", kind=ManagedService.Kind.REDIS, variant="elasticache")
    for n in range(10):
        _svc(a, f"drop-{n:02d}")

    names = _walk(a, limit=7, search="keep-")

    assert len(names) == 40
    assert names == _db_order(a, name__startswith="keep-")


# ---------------------------------------------------------------------------
# Tenancy + row-set parity with the deprecated list field
# ---------------------------------------------------------------------------


def test_other_orgs_services_are_invisible_in_items_and_total(permission_resolver):
    """ManagedService has no org column — it reaches the tenant through the
    owning app, and app slugs recur across orgs. Without the org clause an
    identically-slugged app in another tenant leaks its bindings (#1042 /
    #1183)."""
    permission_resolver.grant(Permission.APP_READ)
    a = _graph("a", app_slug="shared-app")
    b = _graph("b", app_slug="shared-app")
    _svc(a, "ours")
    _svc(b, "theirs")
    _svc(b, "theirs-too", kind=ManagedService.Kind.REDIS, variant="elasticache")

    page = _page(a, limit=50)

    assert [i.name for i in page.items] == ["ours"]
    assert page.total_count == 1, "the count leaked the other org's rows"


def test_soft_deleted_services_are_excluded(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    a = _graph("a")
    _svc(a, "live")
    gone = _svc(a, "gone")
    gone.soft_delete()

    page = _page(a, limit=50)

    assert [i.name for i in page.items] == ["live"]
    assert page.total_count == 1


def test_page_and_deprecated_list_field_agree_on_the_row_set(permission_resolver):
    """Both surfaces are built from ``_managed_services_qs``, so the org
    scope / soft-delete / env filters cannot drift apart. This pins it."""
    permission_resolver.grant(Permission.APP_READ)
    a = _graph("a", app_slug="shared-app")
    b = _graph("b", app_slug="shared-app")
    staging = _extra_env(a, "staging")
    for n in range(6):
        _svc(a, f"prod-{n}")
    _svc(a, "staged", env=staging)
    _svc(a, "removed").soft_delete()
    _svc(b, "foreign")

    with tenant_context(TenantContext(organization_id=a.org.id)):
        listed = ServicesQuery().astrolift_managed_services(_info(), app_slug="shared-app")

    assert [i.name for i in listed] == _walk(a, limit=3)


def test_no_tenant_context_is_refused_outright(permission_resolver):
    """Fails closed rather than returning every org's services (#1183).

    ``@tenant_scoped`` rejects first; the builder's own ``org_id is None``
    branch is defence in depth behind it. Both have to hold."""
    from core.decorators import TenantRequired

    permission_resolver.grant(Permission.APP_READ)
    a = _graph("a")
    _svc(a, "secret-db")

    with tenant_context(TenantContext(organization_id=None)):
        with pytest.raises(TenantRequired):
            ServicesQuery().astrolift_managed_services_page(_info(), app_slug=a.app.slug)


def test_builder_matches_nothing_without_a_tenant():
    """The deny-by-default half of the pair above: if the decorator were
    ever removed, the builder itself must still return no rows — never an
    unscoped queryset over every tenant's services."""
    a = _graph("a")
    _svc(a, "secret-db")

    assert _managed_services_qs(app_slug=a.app.slug, environment_name=None).count() == 0
