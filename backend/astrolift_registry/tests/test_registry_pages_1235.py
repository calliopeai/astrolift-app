"""Cursor pagination over the registry list surfaces (#1235).

Two conversions, two different failure modes:

* ``astroliftWorkloads`` capped at 200 rows with no way to reach the
  201st. Four surfaces read that single field (the app workloads tab,
  /functions, /tasks, and the /jobs schedule list), so the cap
  truncated all of them at once.
* ``astroliftAppTeamAccesses`` had no cap at all — an app shared across
  a large org's teams returned every grant in one response.

Both now share their queryset builder with the page field, so the two
can't drift about which rows exist; the tests below pin the row set,
the walk, the count, and the tenant boundary on each.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import AppTeamAccess, RegisteredApp, Workload
from astrolift_registry.schema.queries import (
    RegistryQuery,
    _app_team_accesses_qs,
    _workloads_qs,
)
from core.decorators import TenantRequired
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _scaffold(*, org_slug: str = "acme", app_slug: str = "hello-app"):
    """An org with one team, one project, and one app under both."""
    org = Organization.objects.create(name=org_slug.title(), slug=org_slug)
    team = Team.objects.create(organization=org, name="Engineering", slug=f"{org_slug}-eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"{org_slug}-demo")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name=app_slug.replace("-", " ").title(),
        slug=app_slug,
    )
    return org, team, app


def _second_app(org, team, *, slug: str):
    project = Project.objects.create(organization=org, team=team, name=slug, slug=f"proj-{slug}")
    return RegisteredApp.objects.create(organization=org, team=team, project=project, name=slug, slug=slug)


def _workload(app, slug, **kwargs):
    return Workload.objects.create(
        registered_app=app,
        name=kwargs.pop("name", slug),
        slug=slug,
        kind=kwargs.pop("kind", Workload.Kind.DEPLOYMENT.value),
        **kwargs,
    )


def _grant(app, team, level=AppTeamAccess.AccessLevel.DEPLOYER.value):
    return AppTeamAccess.objects.create(registered_app=app, team=team, access_level=level)


def _extra_team(org, slug, *, name=None):
    return Team.objects.create(organization=org, name=name or slug.title(), slug=slug)


# ---------------------------------------------------------------------------
# astroliftWorkloadsPage
# ---------------------------------------------------------------------------


def _walk_workloads(query, org, *, limit, **kwargs):
    """Page through the whole workload stream, returning every slug in order."""
    slugs: list[str] = []
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=org.id)):
        for _ in range(50):  # bounded so a non-terminating walk fails loudly
            page = query.astrolift_workloads_page(_info(), limit=limit, after=cursor, **kwargs)
            slugs.extend(w.slug for w in page.items)
            cursor = page.next_cursor
            if cursor is None:
                return slugs
    raise AssertionError("workloads walk did not terminate")


def test_workloads_page_reaches_past_the_old_two_hundred_row_cap(permission_resolver):
    """The regression that motivated the epic: with the list field, the
    201st workload did not exist as far as the UI was concerned."""
    permission_resolver.grant(Permission.APP_READ)
    org, _team, app = _scaffold()
    for n in range(205):
        _workload(app, f"w{n:03d}")

    query = RegistryQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        capped = query.astrolift_workloads(_info(), app_slug="hello-app")
    assert len(capped) == 200, "precondition: the list field still caps"

    slugs = _walk_workloads(query, org, limit=50)
    assert len(slugs) == 205, "the walk lost rows"
    assert len(set(slugs)) == 205, "a row was served twice"


def test_workloads_walk_is_newest_first_and_loses_nothing(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    org, _team, app = _scaffold()
    for n in range(7):
        _workload(app, f"w{n}")

    expected = list(
        Workload.objects.filter(registered_app=app)
        .order_by("-created_at", "-guid")
        .values_list("slug", flat=True)
    )
    # Asserted against the DB's own ordering, not creation order: rows
    # written in a tight loop share a created_at, so ``-guid`` is what
    # actually separates them and the walk has to agree with it.
    assert _walk_workloads(RegistryQuery(), org, limit=3) == expected


def test_workloads_walk_terminates_on_an_exact_page_boundary(permission_resolver):
    """Six rows at limit 3: the second page is full, so the walk can only
    know it is done from the overfetched row. A cursor issued off
    ``len(items) == limit`` would hand out a token to an empty page."""
    permission_resolver.grant(Permission.APP_READ)
    org, _team, app = _scaffold()
    for n in range(6):
        _workload(app, f"w{n}")

    query = RegistryQuery()
    seen: list[str] = []
    pages = 0
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=org.id)):
        for _ in range(10):
            page = query.astrolift_workloads_page(_info(), limit=3, after=cursor)
            pages += 1
            seen.extend(w.slug for w in page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        else:
            raise AssertionError("walk did not terminate")

    assert pages == 2, "the last full page should not hand out a cursor"
    assert len(seen) == 6
    assert len(set(seen)) == 6


def test_workloads_total_count_is_the_whole_result_set(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    org, _team, app = _scaffold()
    for n in range(12):
        _workload(app, f"w{n}")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = RegistryQuery().astrolift_workloads_page(_info(), limit=5)
    assert len(page.items) == 5
    assert page.total_count == 12


def test_workloads_app_slug_filter_narrows_items_and_count(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    org, team, app = _scaffold()
    other = _second_app(org, team, slug="other-app")
    _workload(app, "web")
    _workload(other, "worker")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = RegistryQuery().astrolift_workloads_page(_info(), app_slug="other-app")
    assert [w.slug for w in page.items] == ["worker"]
    assert page.total_count == 1


def test_workloads_search_matches_name_slug_kind_and_app_slug(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    org, team, app = _scaffold()
    billing = _second_app(org, team, slug="billing-svc")
    _workload(app, "web", name="Web frontend")
    _workload(app, "nightly-report", kind=Workload.Kind.CRONJOB.value)
    _workload(billing, "api", name="Billing API")

    query = RegistryQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        by_name = query.astrolift_workloads_page(_info(), search="frontend")
        by_slug = query.astrolift_workloads_page(_info(), search="nightly")
        by_kind = query.astrolift_workloads_page(_info(), search="cronjob")
        by_app_slug = query.astrolift_workloads_page(_info(), search="billing-svc")

    assert [w.slug for w in by_name.items] == ["web"]
    assert [w.slug for w in by_slug.items] == ["nightly-report"]
    assert [w.slug for w in by_kind.items] == ["nightly-report"]
    assert [w.slug for w in by_app_slug.items] == ["api"]


def test_workloads_search_narrows_total_count_not_just_the_page(permission_resolver):
    """A count that ignored the search would render "3 results" over a
    one-row table."""
    permission_resolver.grant(Permission.APP_READ)
    org, _team, app = _scaffold()
    _workload(app, "keep-me")
    _workload(app, "other-1")
    _workload(app, "other-2")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = RegistryQuery().astrolift_workloads_page(_info(), search="keep-me")
    assert [w.slug for w in page.items] == ["keep-me"]
    assert page.total_count == 1


def test_other_orgs_workloads_are_invisible(permission_resolver):
    """Workload reaches the org through registered_app and its manager is
    not tenant-aware, so the scope is the resolver's job (#1183). App
    slugs are unique only within an org, so the sibling org deliberately
    reuses ours."""
    permission_resolver.grant(Permission.APP_READ)
    org, _team, app = _scaffold()
    _workload(app, "ours")
    _other_org, _other_team, other_app = _scaffold(org_slug="rival", app_slug="hello-app")
    _workload(other_app, "theirs")

    query = RegistryQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        unfiltered = query.astrolift_workloads_page(_info(), limit=50)
        by_slug = query.astrolift_workloads_page(_info(), app_slug="hello-app", limit=50)

    assert [w.slug for w in unfiltered.items] == ["ours"]
    assert unfiltered.total_count == 1, "the count leaked the other org's row"
    assert [w.slug for w in by_slug.items] == ["ours"]
    assert by_slug.total_count == 1, "a known sibling-org app slug reached across the tenant boundary"


def test_workloads_page_refuses_without_tenant_context(permission_resolver):
    """Fails closed rather than returning every org's workloads (#1183).

    ``@tenant_scoped`` rejects first; ``_workloads_qs``'s own ``org_id is
    None`` branch is defence in depth behind it.
    """
    permission_resolver.grant(Permission.APP_READ)
    _org, _team, app = _scaffold()
    _workload(app, "web")

    with tenant_context(TenantContext(organization_id=None)):
        with pytest.raises(TenantRequired):
            RegistryQuery().astrolift_workloads_page(_info(), limit=50)


def test_workloads_qs_matches_nothing_without_an_org(permission_resolver):
    """The inner half of the fail-closed pair, reached directly: if the
    decorator is ever relaxed the queryset must still be empty, not
    every org's rows."""
    permission_resolver.grant(Permission.APP_READ)
    _org, _team, app = _scaffold()
    _workload(app, "web")

    with tenant_context(TenantContext(organization_id=None)):
        assert not _workloads_qs(app_slug=None).exists()


# ---------------------------------------------------------------------------
# astroliftAppTeamAccessesPage
# ---------------------------------------------------------------------------


def _walk_team_accesses(query, org, *, limit, **kwargs):
    """Page through the whole grant list, returning every team slug in order."""
    slugs: list[str] = []
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=org.id)):
        for _ in range(50):  # bounded so a non-terminating walk fails loudly
            page = query.astrolift_app_team_accesses_page(_info(), limit=limit, after=cursor, **kwargs)
            slugs.extend(row.team_slug for row in page.items)
            cursor = page.next_cursor
            if cursor is None:
                return slugs
    raise AssertionError("team-access walk did not terminate")


def test_team_accesses_walk_is_alphabetical_and_loses_nothing(permission_resolver):
    """The page keeps the list field's ``team__slug`` ascending order —
    this is a roster, looked up by name, not a feed."""
    permission_resolver.grant(Permission.APP_READ)
    org, home_team, app = _scaffold()
    _grant(app, home_team, AppTeamAccess.AccessLevel.OWNER.value)
    # Deliberately granted out of alphabetical order.
    for slug in ("zeta", "alpha", "mike", "bravo", "yankee", "delta"):
        _grant(app, _extra_team(org, slug))

    expected = list(
        AppTeamAccess.objects.filter(registered_app=app)
        .order_by("team__slug", "guid")
        .values_list("team__slug", flat=True)
    )
    assert len(expected) == 7
    walked = _walk_team_accesses(RegistryQuery(), org, limit=2, app_slug="hello-app")
    assert walked == expected
    assert len(set(walked)) == 7, "a row was served twice"


def test_team_accesses_page_agrees_with_the_deprecated_list_field(permission_resolver):
    """Both fields build on one queryset, so their row set and order
    cannot drift — that shared builder is the point of the conversion."""
    permission_resolver.grant(Permission.APP_READ)
    org, home_team, app = _scaffold()
    _grant(app, home_team, AppTeamAccess.AccessLevel.OWNER.value)
    for slug in ("charlie", "alpha", "bravo"):
        _grant(app, _extra_team(org, slug))

    query = RegistryQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        listed = query.astrolift_app_team_accesses(_info(), app_slug="hello-app")
    walked = _walk_team_accesses(query, org, limit=2, app_slug="hello-app")
    assert [row.team_slug for row in listed] == walked


def test_team_accesses_total_count_is_the_whole_result_set(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    org, home_team, app = _scaffold()
    _grant(app, home_team, AppTeamAccess.AccessLevel.OWNER.value)
    for n in range(9):
        _grant(app, _extra_team(org, f"team-{n}"))

    with tenant_context(TenantContext(organization_id=org.id)):
        page = RegistryQuery().astrolift_app_team_accesses_page(_info(), app_slug="hello-app", limit=4)
    assert len(page.items) == 4
    assert page.total_count == 10


def test_team_accesses_search_narrows_total_count_not_just_the_page(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    org, home_team, app = _scaffold()
    _grant(app, home_team, AppTeamAccess.AccessLevel.OWNER.value)
    _grant(app, _extra_team(org, "payments", name="Payments Platform"))
    _grant(app, _extra_team(org, "search-infra"))
    _grant(app, _extra_team(org, "growth"))

    query = RegistryQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        by_slug = query.astrolift_app_team_accesses_page(_info(), app_slug="hello-app", search="search-")
        by_name = query.astrolift_app_team_accesses_page(_info(), app_slug="hello-app", search="platform")

    assert [row.team_slug for row in by_slug.items] == ["search-infra"]
    assert by_slug.total_count == 1
    assert [row.team_slug for row in by_name.items] == ["payments"]
    assert by_name.total_count == 1


def test_team_accesses_page_marks_the_home_team(permission_resolver):
    """``is_home`` used to come from a separate app fetch; it now rides on
    the joined row. Same answer, one query fewer — pinned here."""
    permission_resolver.grant(Permission.APP_READ)
    org, home_team, app = _scaffold()
    _grant(app, home_team, AppTeamAccess.AccessLevel.OWNER.value)
    _grant(app, _extra_team(org, "zulu"))

    with tenant_context(TenantContext(organization_id=org.id)):
        page = RegistryQuery().astrolift_app_team_accesses_page(_info(), app_slug="hello-app")

    by_slug = {row.team_slug: row for row in page.items}
    assert by_slug[home_team.slug].is_home is True
    assert by_slug["zulu"].is_home is False


def test_team_accesses_page_hides_revoked_grants(permission_resolver):
    """A revoked grant is soft-deleted; it must leave both the page and
    the count."""
    permission_resolver.grant(Permission.APP_READ)
    org, home_team, app = _scaffold()
    _grant(app, home_team, AppTeamAccess.AccessLevel.OWNER.value)
    revoked = _grant(app, _extra_team(org, "former"))
    revoked.soft_delete()

    with tenant_context(TenantContext(organization_id=org.id)):
        page = RegistryQuery().astrolift_app_team_accesses_page(_info(), app_slug="hello-app")

    assert [row.team_slug for row in page.items] == [home_team.slug]
    assert page.total_count == 1


def test_team_accesses_page_hides_deregistered_apps(permission_resolver):
    """The list field resolved the app with ``deleted_at__isnull=True``;
    the join has to carry that or a torn-down app keeps answering."""
    permission_resolver.grant(Permission.APP_READ)
    org, home_team, app = _scaffold()
    _grant(app, home_team, AppTeamAccess.AccessLevel.OWNER.value)
    app.soft_delete()

    query = RegistryQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        page = query.astrolift_app_team_accesses_page(_info(), app_slug="hello-app")
        listed = query.astrolift_app_team_accesses(_info(), app_slug="hello-app")

    assert page.items == []
    assert page.total_count == 0
    assert listed == []


def test_other_orgs_team_accesses_are_invisible(permission_resolver):
    """AppTeamAccess has no org FK; it reaches the tenant through
    registered_app, and app slugs are unique only within an org — so the
    sibling org reuses ours to prove the join is the boundary."""
    permission_resolver.grant(Permission.APP_READ)
    org, home_team, app = _scaffold()
    _grant(app, home_team, AppTeamAccess.AccessLevel.OWNER.value)

    other_org, other_home, other_app = _scaffold(org_slug="rival", app_slug="hello-app")
    _grant(other_app, other_home, AppTeamAccess.AccessLevel.OWNER.value)
    _grant(other_app, _extra_team(other_org, "aaa-first-alphabetically"))

    with tenant_context(TenantContext(organization_id=org.id)):
        page = RegistryQuery().astrolift_app_team_accesses_page(_info(), app_slug="hello-app")

    assert [row.team_slug for row in page.items] == [home_team.slug]
    assert page.total_count == 1, "the count leaked the other org's grants"


def test_team_accesses_page_refuses_without_tenant_context(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    _org, home_team, app = _scaffold()
    _grant(app, home_team, AppTeamAccess.AccessLevel.OWNER.value)

    with tenant_context(TenantContext(organization_id=None)):
        with pytest.raises(TenantRequired):
            RegistryQuery().astrolift_app_team_accesses_page(_info(), app_slug="hello-app")


def test_team_accesses_qs_matches_nothing_without_an_org(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    _org, home_team, app = _scaffold()
    _grant(app, home_team, AppTeamAccess.AccessLevel.OWNER.value)

    with tenant_context(TenantContext(organization_id=None)):
        assert not _app_team_accesses_qs(app_slug="hello-app").exists()
