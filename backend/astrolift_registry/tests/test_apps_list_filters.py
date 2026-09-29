"""Tests for the apps-list server-side filter + cursor pagination (#481).

Covers:

* Filter args narrow correctly on each axis (search, team, project,
  source_kind, status) and combine intersectionally.
* Default args preserve back-compat — no filters applied, legacy shape.
* Cursor pagination returns stable, non-overlapping pages across
  cursor walks.
* N+1 protection — the page query runs a bounded number of DB
  roundtrips regardless of app count (cheap path: 1 page + 1 count;
  freshness path: +2 for the deploy rollup).

Real Postgres, no mocks. The freshness behaviour is asserted live so
the rollup-driven status filter walks the same code path it would in
prod.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Role, RoleBinding, Team
from astrolift_lifecycle.models import AppEnvironment, Deployment, PreviewEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.queries import RegistryQuery, _decode_apps_cursor
from astrolift_registry.schema.types import (
    STALE_DEPLOY_WINDOW_DAYS,
    AppsListSortKey,
    AstroliftAppListStatusFilter,
    AstroliftAppSourceKindFilter,
)
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None))


def _user(username: str, **kw):
    User = get_user_model()
    return User.objects.create(username=username, email=f"{username}@test", **kw)


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


def _provider_plugin(slug: str):
    [plugin] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name=f"Plugin {slug}",
                slug=f"filters-plugin-{slug}",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    return plugin


def _cluster(org, plugin, *, slug: str):
    return TenantCluster.objects.create(
        organization=org,
        name=f"cluster-{slug}",
        slug=f"filters-cluster-{slug}",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


def _scaffold(suffix: str):
    """Build a tenant with two teams + two projects so the team /
    project filter axes have something distinct to point at.

    Apps cover the full pulse axis (``ok`` / ``failed`` / ``stale`` /
    ``never``) on team-A/project-A so the status filter has a
    deterministic distribution to assert against. team-B/project-B
    adds a single ``ok`` row so cross-team filters reveal isolation.
    """
    org = Organization.objects.create(name=f"Acme{suffix}", slug=f"filt-acme{suffix}")
    team_a = Team.objects.create(organization=org, name="Plat", slug=f"plat{suffix}")
    team_b = Team.objects.create(organization=org, name="Ops", slug=f"ops{suffix}")
    project_a = Project.objects.create(organization=org, team=team_a, name="Demo", slug=f"demo{suffix}")
    project_b = Project.objects.create(organization=org, team=team_b, name="Infra", slug=f"infra{suffix}")
    plugin = _provider_plugin(suffix)
    cluster = _cluster(org, plugin, slug=suffix)

    apps: dict[str, RegisteredApp] = {}

    def make_app(
        key: str,
        *,
        name: str,
        slug: str,
        team: Team,
        project: Project,
        source_kind: str = "github",
        source_repo: str = "",
        description: str = "",
    ) -> RegisteredApp:
        app = RegisteredApp.objects.create(
            organization=org,
            team=team,
            project=project,
            name=name,
            slug=slug,
            source_kind=source_kind,
            source_repo=source_repo,
            description=description,
        )
        apps[key] = app
        return app

    make_app(
        "ok-a",
        name="Alpha",
        slug=f"alpha-app{suffix}",
        team=team_a,
        project=project_a,
        source_repo="acme/alpha",
        description="Mission-critical billing service",
    )
    make_app(
        "failed-a",
        name="Bravo",
        slug=f"bravo-app{suffix}",
        team=team_a,
        project=project_a,
        source_kind="gitlab",
        source_repo="acme/bravo",
    )
    make_app(
        "stale-a",
        name="Charlie",
        slug=f"charlie-app{suffix}",
        team=team_a,
        project=project_a,
        source_kind="bitbucket",
        source_repo="acme/charlie",
    )
    make_app(
        "never-a",
        name="Delta",
        slug=f"delta-app{suffix}",
        team=team_a,
        project=project_a,
        source_kind="github",
        source_repo="acme/delta",
    )
    make_app(
        "ok-b",
        name="Echo",
        slug=f"echo-app{suffix}",
        team=team_b,
        project=project_b,
        source_kind="github",
        source_repo="acme/echo",
    )

    envs: dict[str, AppEnvironment] = {
        key: AppEnvironment.objects.create(
            registered_app=app,
            tenant_cluster=cluster,
            name="prod",
            url="https://x.example.com",
            required_approvals=0,
        )
        for key, app in apps.items()
    }

    # Deploy rows to drive the pulse axis. _make_deploy backdates the
    # row so STALE registers without waiting for wall-clock.
    _make_deploy(apps["ok-a"], envs["ok-a"], status="running", age=timedelta(hours=2))
    _make_deploy(apps["ok-b"], envs["ok-b"], status="running", age=timedelta(hours=2))
    _make_deploy(apps["failed-a"], envs["failed-a"], status="failed", age=timedelta(hours=4))
    _make_deploy(
        apps["stale-a"],
        envs["stale-a"],
        status="running",
        age=timedelta(days=STALE_DEPLOY_WINDOW_DAYS + 3),
    )
    # never-a deliberately has no deploys

    role = Role.objects.create(
        name=f"reader{suffix}",
        slug=f"reader-filter{suffix}",
        permissions=["app.read"],
    )
    return SimpleNamespace(
        org=org,
        team_a=team_a,
        team_b=team_b,
        project_a=project_a,
        project_b=project_b,
        cluster=cluster,
        apps=apps,
        envs=envs,
        role=role,
    )


def _make_deploy(app, env, *, status: str, age: timedelta, trigger_kind: str = "manual"):
    """Create a Deployment row pinned to ``now - age`` (mirrors the
    helper in test_apps_freshness)."""
    deploy = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=trigger_kind,
        status=status,
    )
    target = timezone.now() - age
    Deployment.objects.filter(pk=deploy.pk).update(created_at=target)
    deploy.refresh_from_db()
    return deploy


def _superuser(username: str):
    return _user(username, is_superuser=True, is_staff=True)


def _run_in_tenant(ctx_kw: dict, fn):
    with tenant_context(TenantContext(**ctx_kw)):
        return fn()


# ---------- back-compat ------------------------------------------------


def test_default_args_preserve_legacy_shape():
    scaffold = _scaffold("-default")
    user = _superuser("default-filter")

    result = _run_in_tenant(
        {"organization_id": scaffold.org.id, "actor_user_id": user.id},
        lambda: RegistryQuery().astrolift_apps(_info()),
    )

    slugs = {a.slug for a in result}
    expected = {scaffold.apps[k].slug for k in ("ok-a", "failed-a", "stale-a", "never-a", "ok-b")}
    assert slugs == expected
    for row in result:
        assert row.health_pulse is None  # freshness still opt-in


# ---------- single-axis filters ----------------------------------------


def test_search_matches_name_slug_repo_description_case_insensitive():
    scaffold = _scaffold("-search")
    user = _superuser("search-user")

    # ``alpha`` is in the name + slug + repo; ``billing`` is only in the
    # description.
    by_name = _run_in_tenant(
        {"organization_id": scaffold.org.id, "actor_user_id": user.id},
        lambda: RegistryQuery().astrolift_apps(_info(), search="ALPHA"),
    )
    assert {a.slug for a in by_name} == {scaffold.apps["ok-a"].slug}

    by_description = _run_in_tenant(
        {"organization_id": scaffold.org.id, "actor_user_id": user.id},
        lambda: RegistryQuery().astrolift_apps(_info(), search="billing"),
    )
    assert {a.slug for a in by_description} == {scaffold.apps["ok-a"].slug}

    by_repo = _run_in_tenant(
        {"organization_id": scaffold.org.id, "actor_user_id": user.id},
        lambda: RegistryQuery().astrolift_apps(_info(), search="acme/bravo"),
    )
    assert {a.slug for a in by_repo} == {scaffold.apps["failed-a"].slug}


def test_team_slug_filter_isolates_owning_team():
    scaffold = _scaffold("-team")
    user = _superuser("team-user")

    result = _run_in_tenant(
        {"organization_id": scaffold.org.id, "actor_user_id": user.id},
        lambda: RegistryQuery().astrolift_apps(_info(), team_slug=scaffold.team_b.slug),
    )
    assert {a.slug for a in result} == {scaffold.apps["ok-b"].slug}


def test_project_slug_filter_isolates_owning_project():
    scaffold = _scaffold("-project")
    user = _superuser("project-user")

    result = _run_in_tenant(
        {"organization_id": scaffold.org.id, "actor_user_id": user.id},
        lambda: RegistryQuery().astrolift_apps(_info(), project_slug=scaffold.project_b.slug),
    )
    assert {a.slug for a in result} == {scaffold.apps["ok-b"].slug}


def test_source_kind_filter_isolates_host():
    scaffold = _scaffold("-source")
    user = _superuser("source-user")

    result = _run_in_tenant(
        {"organization_id": scaffold.org.id, "actor_user_id": user.id},
        lambda: RegistryQuery().astrolift_apps(_info(), source_kind=AstroliftAppSourceKindFilter.GITLAB),
    )
    assert {a.slug for a in result} == {scaffold.apps["failed-a"].slug}


def test_status_filter_each_bucket():
    """Each pulse bucket must return exactly the rows whose pulse
    matches the requested filter — ``OK`` / ``DEGRADED`` / ``STALE`` /
    ``NEVER_DEPLOYED``. The fixture deliberately seeds one row per
    bucket on team-A so the result set is unambiguous."""
    scaffold = _scaffold("-status")
    user = _superuser("status-user")

    def run(status: AstroliftAppListStatusFilter):
        return _run_in_tenant(
            {"organization_id": scaffold.org.id, "actor_user_id": user.id},
            lambda: RegistryQuery().astrolift_apps(_info(), status=status),
        )

    assert {a.slug for a in run(AstroliftAppListStatusFilter.DEGRADED)} == {scaffold.apps["failed-a"].slug}
    assert {a.slug for a in run(AstroliftAppListStatusFilter.STALE)} == {scaffold.apps["stale-a"].slug}
    assert {a.slug for a in run(AstroliftAppListStatusFilter.NEVER_DEPLOYED)} == {
        scaffold.apps["never-a"].slug
    }
    # OK should pick up both successful deploys regardless of team.
    assert {a.slug for a in run(AstroliftAppListStatusFilter.OK)} == {
        scaffold.apps["ok-a"].slug,
        scaffold.apps["ok-b"].slug,
    }


# ---------- combined filters -------------------------------------------


def test_filters_combine_intersectionally():
    """search ∩ team_slug ∩ status — each axis must narrow the row set."""
    scaffold = _scaffold("-combo")
    user = _superuser("combo-user")

    # team_a's "acme" repos with pulse=OK should be exactly ``ok-a``.
    result = _run_in_tenant(
        {"organization_id": scaffold.org.id, "actor_user_id": user.id},
        lambda: RegistryQuery().astrolift_apps(
            _info(),
            search="acme",
            team_slug=scaffold.team_a.slug,
            status=AstroliftAppListStatusFilter.OK,
        ),
    )
    assert {a.slug for a in result} == {scaffold.apps["ok-a"].slug}


# ---------- cursor pagination ------------------------------------------


def test_cursor_pagination_walks_all_rows_without_overlap():
    """Walking ``astrolift_apps_page`` with the returned cursor must
    visit every row exactly once, in newest-first order."""
    scaffold = _scaffold("-page")
    user = _superuser("page-user")

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    with tenant_context(TenantContext(organization_id=scaffold.org.id, actor_user_id=user.id)):
        while True:
            page = RegistryQuery().astrolift_apps_page(_info(), cursor=cursor, limit=2)
            assert page.total_count == len(scaffold.apps)
            for row in page.items:
                seen.append(row.slug)
            pages += 1
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
            assert _decode_apps_cursor(cursor, AppsListSortKey.CREATED_DESC) is not None
            # Defensive infinite-loop guard.
            assert pages < 10

    assert len(seen) == len(scaffold.apps)
    assert set(seen) == {a.slug for a in scaffold.apps.values()}
    assert len(seen) == len(set(seen))  # no duplicates across pages
    assert pages >= 2  # actually paginated


def test_cursor_pagination_respects_status_filter_across_pages():
    """Status-filtered pagination still walks every matching row across
    successive cursor fetches — the filter is post-DB but cursors
    re-anchor on the last kept row so the next page picks up where
    the previous left off."""
    scaffold = _scaffold("-page-status")
    user = _superuser("page-status-user")

    # Add a few more ``ok`` apps so the page actually has to walk.
    extra_envs = []
    for i in range(3):
        app = RegisteredApp.objects.create(
            organization=scaffold.org,
            team=scaffold.team_a,
            project=scaffold.project_a,
            name=f"Extra {i}",
            slug=f"extra-ok-{i}-page-status",
            source_kind="github",
            source_repo=f"acme/extra-{i}",
        )
        env = AppEnvironment.objects.create(
            registered_app=app,
            tenant_cluster=scaffold.cluster,
            name="prod",
            url="https://x.example.com",
            required_approvals=0,
        )
        _make_deploy(app, env, status="running", age=timedelta(hours=1 + i))
        extra_envs.append(env)

    seen: list[str] = []
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=scaffold.org.id, actor_user_id=user.id)):
        for _ in range(10):
            page = RegistryQuery().astrolift_apps_page(
                _info(),
                status=AstroliftAppListStatusFilter.OK,
                cursor=cursor,
                limit=2,
            )
            for row in page.items:
                seen.append(row.slug)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
    # ok-a + ok-b + the 3 extras = 5 rows
    assert len(seen) == 5
    assert len(set(seen)) == 5


# ---------- N+1 protection ---------------------------------------------


def test_page_query_count_independent_of_app_count():
    """The page resolver runs O(1) DB queries — the cheap path is
    1 select_related fetch + 1 count(); the freshness path adds the
    same 2 deploy queries the freshness rollup already pays."""
    scaffold = _scaffold("-nplus1")
    user = _superuser("nplus1-page-user")

    # Pile on apps so a per-row regression would show up loudly.
    for i in range(8):
        RegisteredApp.objects.create(
            organization=scaffold.org,
            team=scaffold.team_a,
            project=scaffold.project_a,
            name=f"Bulk {i}",
            slug=f"bulk-{i}-nplus1",
            source_kind="github",
        )

    with tenant_context(TenantContext(organization_id=scaffold.org.id, actor_user_id=user.id)):
        with CaptureQueriesContext(connection) as small_ctx:
            small = RegistryQuery().astrolift_apps_page(_info(), limit=3)
        with CaptureQueriesContext(connection) as full_ctx:
            full = RegistryQuery().astrolift_apps_page(_info(), limit=100)

    # Same number of queries regardless of how many rows we materialise.
    # That's the N+1 contract — the page builder's prefetch on
    # ``approver_users`` keeps the M2M cost constant.
    assert len(small_ctx.captured_queries) == len(full_ctx.captured_queries), [
        q["sql"] for q in full_ctx.captured_queries
    ]
    assert small.total_count == full.total_count
    # And the cheap path is genuinely cheap — the resolver only fires
    # a small constant number of queries (page + count +
    # prefetch + viewer/permission checks + #730's active-preview-count
    # aggregate + #1920's bulk viewer-permissions lookup, which
    # ``app_to_type`` needs to decide whether to mask [env] secret
    # values per row, plus #2149's two bulk reads for the rows'
    # topologyKind and clusterSlugs). The bound is intentionally a
    # constant, not a per-row scaling; N+1 protection is the ``small == full``
    # assertion above; this is the absolute-cheapness assertion.
    assert len(small_ctx.captured_queries) <= 8, [q["sql"] for q in small_ctx.captured_queries]


# ---------- my-apps parity ---------------------------------------------


def test_my_apps_page_honours_filters_and_viewer_scope():
    """The viewer-scoped page resolver must compose scope ∩ filter and
    return a page envelope (not a flat list). With an APP-scoped
    binding on a single app, no other rows should ever surface."""
    scaffold = _scaffold("-my-page")
    user = _user("my-page-viewer")
    RoleBinding.objects.create(
        user=user,
        role=scaffold.role,
        scope_kind=RoleBinding.ScopeKind.APP,
        scope_id=scaffold.apps["ok-a"].pk,
    )

    page = _run_in_tenant(
        {"organization_id": scaffold.org.id, "actor_user_id": user.id},
        lambda: RegistryQuery().astrolift_my_apps_page(_info(), limit=10),
    )

    assert page.total_count == 1
    assert [a.slug for a in page.items] == [scaffold.apps["ok-a"].slug]
    assert page.next_cursor is None


# ---------- activePreviewCount (#730) --------------------------------


def test_active_preview_count_only_includes_live_previews():
    """The page resolver surfaces ``activePreviewCount`` per row, counting
    only PreviewEnvironment rows whose ``torn_down_at`` is null. Apps
    with no live previews surface 0; torn-down rows are excluded."""
    scaffold = _scaffold("-prevct")
    user = _superuser("prevct-user")
    ok_app = scaffold.apps["ok-a"]
    ok_env = scaffold.envs["ok-a"]
    other_app = scaffold.apps["failed-a"]

    # 2 live + 1 torn-down on ok_app; 0 on other_app.
    PreviewEnvironment.objects.create(
        registered_app=ok_app,
        app_environment=ok_env,
        pr_number=11,
        branch="feat/a",
        hostname=f"pr-11.{ok_app.slug}.example",
        namespace=f"prev-11-{ok_app.slug}",
    )
    PreviewEnvironment.objects.create(
        registered_app=ok_app,
        app_environment=ok_env,
        pr_number=12,
        branch="feat/b",
        hostname=f"pr-12.{ok_app.slug}.example",
        namespace=f"prev-12-{ok_app.slug}",
    )
    torn_down = PreviewEnvironment.objects.create(
        registered_app=ok_app,
        app_environment=ok_env,
        pr_number=13,
        branch="feat/c",
        hostname=f"pr-13.{ok_app.slug}.example",
        namespace=f"prev-13-{ok_app.slug}",
    )
    torn_down.torn_down_at = torn_down.created_at
    torn_down.save(update_fields=["torn_down_at", "updated_at", "version"])

    page = _run_in_tenant(
        {"organization_id": scaffold.org.id, "actor_user_id": user.id},
        lambda: RegistryQuery().astrolift_apps_page(_info(), limit=100),
    )
    by_slug = {a.slug: a for a in page.items}
    assert by_slug[ok_app.slug].active_preview_count == 2
    assert by_slug[other_app.slug].active_preview_count == 0


# ---------- sort_by (#729) ------------------------------------------------


def test_sort_by_name_asc_returns_case_insensitive_order():
    """NAME_ASC produces alphabetical order (case-insensitive) across pages."""
    scaffold = _scaffold("-sort-name")
    user = _superuser("sort-name-user")

    # scaffold has: Alpha, Bravo, Charlie, Delta, Echo
    all_slugs: list[str] = []
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=scaffold.org.id, actor_user_id=user.id)):
        for _ in range(10):
            page = RegistryQuery().astrolift_apps_page(
                _info(),
                sort_by=AppsListSortKey.NAME_ASC,
                cursor=cursor,
                limit=2,
            )
            all_slugs.extend(a.slug for a in page.items)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor

    assert len(all_slugs) == 5
    # Names returned in case-insensitive alphabetical order
    names_in_order = [s.split("-")[0].capitalize() for s in all_slugs]
    assert names_in_order == sorted(names_in_order, key=str.lower)


def test_sort_by_deployed_desc_most_recent_first_nulls_last():
    """DEPLOYED_DESC puts recently-deployed apps first; never-deployed last."""
    scaffold = _scaffold("-sort-deploy")
    user = _superuser("sort-deploy-user")

    # ok-a: running deploy 2h ago
    # ok-b: running deploy 2h ago (also)
    # stale-a: running deploy >30d ago
    # failed-a: failed deploy (no running), never-deployed == no running deploy
    # never-a: no deploys at all
    with tenant_context(TenantContext(organization_id=scaffold.org.id, actor_user_id=user.id)):
        page = RegistryQuery().astrolift_apps_page(
            _info(),
            sort_by=AppsListSortKey.DEPLOYED_DESC,
            limit=100,
        )

    slugs = [a.slug for a in page.items]
    assert len(slugs) == 5

    # ok-a / ok-b must appear before stale-a (older running deploy)
    ok_a_idx = slugs.index(scaffold.apps["ok-a"].slug)
    ok_b_idx = slugs.index(scaffold.apps["ok-b"].slug)
    stale_idx = slugs.index(scaffold.apps["stale-a"].slug)
    # failed-a and never-a have no running deploy → come last
    never_idx = slugs.index(scaffold.apps["never-a"].slug)
    failed_idx = slugs.index(scaffold.apps["failed-a"].slug)

    assert max(ok_a_idx, ok_b_idx) < stale_idx
    assert stale_idx < min(never_idx, failed_idx)


def test_sort_by_cursor_mismatch_restarts_from_page_1():
    """A cursor from one sort mode is silently discarded when sort changes."""
    scaffold = _scaffold("-sort-mismatch")
    user = _superuser("sort-mismatch-user")

    with tenant_context(TenantContext(organization_id=scaffold.org.id, actor_user_id=user.id)):
        # Get a cursor from CREATED_DESC
        page1 = RegistryQuery().astrolift_apps_page(_info(), sort_by=AppsListSortKey.CREATED_DESC, limit=2)
        stale_cursor = page1.next_cursor
        assert stale_cursor is not None

        # Present the CREATED_DESC cursor to NAME_ASC — should restart
        page2 = RegistryQuery().astrolift_apps_page(
            _info(),
            sort_by=AppsListSortKey.NAME_ASC,
            cursor=stale_cursor,
            limit=100,
        )

    # Restarted from page 1 — all 5 apps are present
    assert page2.total_count == 5
    assert len(page2.items) == 5


# ---------- archive filter (#743) -------------------------------------


def test_apps_list_hides_archived_apps():
    """Archived apps are excluded from the standard flat list."""
    scaffold = _scaffold("-archive-flat")
    user = _superuser("archive-flat-user")

    archived = scaffold.apps["ok-a"]
    archived.archived_at = timezone.now()
    archived.save(update_fields=["archived_at"])

    with tenant_context(TenantContext(organization_id=scaffold.org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_apps(_info())

    slugs = {a.slug for a in result}
    assert archived.slug not in slugs
    assert slugs == {scaffold.apps[k].slug for k in ("failed-a", "stale-a", "never-a", "ok-b")}


def test_apps_page_hides_archived_by_default():
    """Cursor-paginated org list hides archived apps unless include_archived=True."""
    scaffold = _scaffold("-archive-page")
    user = _superuser("archive-page-user")

    archived = scaffold.apps["ok-a"]
    archived.archived_at = timezone.now()
    archived.save(update_fields=["archived_at"])

    with tenant_context(TenantContext(organization_id=scaffold.org.id, actor_user_id=user.id)):
        hidden = RegistryQuery().astrolift_apps_page(_info(), limit=100)
        revealed = RegistryQuery().astrolift_apps_page(_info(), limit=100, include_archived=True)

    assert archived.slug not in {a.slug for a in hidden.items}
    assert hidden.total_count == 4
    assert archived.slug in {a.slug for a in revealed.items}
    assert revealed.total_count == 5


def test_my_apps_hides_archived_apps():
    """Viewer-scoped flat list hides archived apps."""
    scaffold = _scaffold("-archive-my")
    # Bind the viewer to the org so my_apps surfaces every row.
    user = _user("archive-my-user")
    RoleBinding.objects.create(
        user=user,
        role=scaffold.role,
        scope_kind=RoleBinding.ScopeKind.ORG,
        scope_id=scaffold.org.id,
    )

    archived = scaffold.apps["ok-b"]
    archived.archived_at = timezone.now()
    archived.save(update_fields=["archived_at"])

    with tenant_context(TenantContext(organization_id=scaffold.org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_my_apps(_info())

    assert archived.slug not in {a.slug for a in result}


def test_my_apps_page_include_archived_opt_in():
    """Viewer-scoped page exposes archived apps via include_archived."""
    scaffold = _scaffold("-archive-my-page")
    user = _user("archive-my-page-user")
    RoleBinding.objects.create(
        user=user,
        role=scaffold.role,
        scope_kind=RoleBinding.ScopeKind.ORG,
        scope_id=scaffold.org.id,
    )

    archived = scaffold.apps["never-a"]
    archived.archived_at = timezone.now()
    archived.save(update_fields=["archived_at"])

    with tenant_context(TenantContext(organization_id=scaffold.org.id, actor_user_id=user.id)):
        hidden = RegistryQuery().astrolift_my_apps_page(_info(), limit=100)
        revealed = RegistryQuery().astrolift_my_apps_page(_info(), limit=100, include_archived=True)

    assert archived.slug not in {a.slug for a in hidden.items}
    assert archived.slug in {a.slug for a in revealed.items}


# ---------- apps / agents separation -----------------------------------


def test_agent_host_apps_excluded_from_apps_list():
    """Apps and Agents are separate entity-modules: a RegisteredApp that hosts
    an agent (has a kind=agent Workload) must NOT appear in the Apps list (it
    shows in the Agents list instead). Regular apps are unaffected."""
    from astrolift_registry.models import Workload

    scaffold = _scaffold("-agentsep")
    user = _superuser("agentsep-user")

    agent_host = scaffold.apps["ok-a"]
    Workload.objects.create(
        registered_app=agent_host,
        name="Agent",
        slug="agent-wl-agentsep",
        kind=Workload.Kind.AGENT,
    )

    result = _run_in_tenant(
        {"organization_id": scaffold.org.id, "actor_user_id": user.id},
        lambda: RegistryQuery().astrolift_apps(_info()),
    )
    slugs = {a.slug for a in result}
    assert agent_host.slug not in slugs, "agent-host app must not appear in the Apps list"
    # Regular apps (no agent workload) still appear.
    assert scaffold.apps["ok-b"].slug in slugs
    assert scaffold.apps["failed-a"].slug in slugs


def test_agent_host_apps_excluded_from_apps_page_count():
    """The cursor page's totalCount also excludes agent-host apps (the
    exclusion lives in the shared filter helper)."""
    from astrolift_registry.models import Workload

    scaffold = _scaffold("-agentsep-pg")
    user = _superuser("agentsep-pg-user")
    Workload.objects.create(
        registered_app=scaffold.apps["ok-a"],
        name="Agent",
        slug="agent-wl-agentsep-pg",
        kind=Workload.Kind.AGENT,
    )
    page = _run_in_tenant(
        {"organization_id": scaffold.org.id, "actor_user_id": user.id},
        lambda: RegistryQuery().astrolift_apps_page(_info(), limit=50),
    )
    slugs = {row.slug for row in page.items}
    assert scaffold.apps["ok-a"].slug not in slugs
    # 5 scaffold apps minus the 1 agent host = 4.
    assert page.total_count == 4


def test_mixed_agent_and_application_host_remains_in_apps_list():
    from astrolift_registry.models import Workload

    scaffold = _scaffold("-agentsep-mixed")
    user = _superuser("agentsep-mixed-user")
    mixed = scaffold.apps["ok-a"]
    Workload.objects.create(
        registered_app=mixed,
        name="Agent",
        slug="agent-wl-agentsep-mixed",
        kind=Workload.Kind.AGENT,
    )
    Workload.objects.create(
        registered_app=mixed,
        name="Web",
        slug="web-wl-agentsep-mixed",
        kind=Workload.Kind.DEPLOYMENT,
    )

    result = _run_in_tenant(
        {"organization_id": scaffold.org.id, "actor_user_id": user.id},
        lambda: RegistryQuery().astrolift_apps(_info()),
    )

    assert mixed.slug in {app.slug for app in result}
