"""Scope-aware per-app permission resolution (#478).

Covers the new ``astroliftAppPermissions(appSlug)`` query and the
``viewerPermissions`` field on ``AstroliftRegisteredApp``. Both surface
the same data — the effective permission slug set the viewer holds on a
specific app, after walking ORG → TEAM → PROJECT → APP scope
inheritance — so mobile and web can render only the actions a viewer
can take instead of round-tripping a try-and-403 per button.

Cases covered:

* Org admin binding → every permission appears on every app.
* Team-scoped binding → permissions inherited at apps under that team
  only; sibling teams' apps remain empty.
* Project-scoped binding → permissions appear only on apps under that
  project, even when the team has other projects.
* App-direct binding → permissions appear only on that one app row.
* No binding → empty list (legitimate, not an error).
* Cross-org / cross-tenant isolation — a binding in a different org
  doesn't grant visibility in the active tenant.
* Expired binding excluded.
* N+1 protection — listing N apps must not produce N permission
  queries (we measure with CaptureQueriesContext).
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from astrolift_identity.models import Organization, Project, Role, RoleBinding, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.queries import RegistryQuery
from core.tenancy import TenantContext, get_current_tenant, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _app_perms(app_slug: str) -> list[str]:
    """Effective permission slugs the active-tenant viewer holds on one app.

    The dedicated ``astroliftAppPermissions`` point query was removed in
    #507 — per-app perms now ride the ``viewerPermissions`` field on the
    apps-list resolvers. This exercises the same live backing logic
    (``resolve_effective_permissions_for_apps``) against a single
    tenant-scoped app, preserving the #478 scope-inheritance coverage.
    """
    from astrolift_identity.permission_resolver import resolve_effective_permissions_for_apps

    tenant = get_current_tenant()
    if tenant is None or tenant.actor_user_id is None:
        return []
    qs = RegisteredApp.objects.filter(slug=app_slug, deleted_at__isnull=True)
    if tenant.organization_id is not None:
        qs = qs.filter(organization_id=tenant.organization_id)
    app = qs.first()
    if app is None:
        return []
    return sorted(resolve_effective_permissions_for_apps(tenant, [app]).get(app.pk, set()))


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


def _role(slug: str, perms: list[str]) -> Role:
    """Create a non-system Role with the given permission slugs.

    Distinct slug per call so the unique-on-slug index doesn't bite
    across tests that share a transaction within the same session.
    """
    return Role.objects.create(
        name=slug,
        slug=slug,
        scope_level=Role.ScopeLevel.ORG,
        permissions=perms,
        is_system=False,
    )


def _scaffold():
    """Build a two-team / two-project tenant with three apps.

    Layout:
      org (acme)
        team_eng
          project_demo
            app alpha
            app beta
        team_ops
          project_tools
            app gamma
    """
    org = Organization.objects.create(name="Acme", slug="acme-perms")
    team_eng = Team.objects.create(organization=org, name="Eng", slug="eng-perms")
    team_ops = Team.objects.create(organization=org, name="Ops", slug="ops-perms")
    project_demo = Project.objects.create(organization=org, team=team_eng, name="Demo", slug="demo-perms")
    project_tools = Project.objects.create(organization=org, team=team_ops, name="Tools", slug="tools-perms")
    apps = {
        "alpha": RegisteredApp.objects.create(
            organization=org, team=team_eng, project=project_demo, name="Alpha", slug="alpha-perms"
        ),
        "beta": RegisteredApp.objects.create(
            organization=org, team=team_eng, project=project_demo, name="Beta", slug="beta-perms"
        ),
        "gamma": RegisteredApp.objects.create(
            organization=org, team=team_ops, project=project_tools, name="Gamma", slug="gamma-perms"
        ),
    }
    return org, team_eng, team_ops, project_demo, project_tools, apps


# ---------- astroliftAppPermissions(appSlug) ----------------------------


def test_org_admin_sees_full_permission_set_on_every_app():
    """An ORG-scoped binding with the admin permission catalog grants
    the full set against every app in the org — inheritance flows down
    from the root scope."""
    org, *_rest, apps = _scaffold()
    user = _user("orgadmin-perms")
    role = _role("org-admin-perms", ["app.read", "app.deploy", "app.delete"])
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.id)

    expected = ["app.delete", "app.deploy", "app.read"]
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        for slug in ("alpha-perms", "beta-perms", "gamma-perms"):
            assert _app_perms(slug) == expected


def test_team_binding_inherits_on_apps_under_that_team():
    """TEAM-scoped binding grants on apps whose ``team_id`` matches —
    sibling teams' apps stay empty even though they live in the same
    org."""
    org, team_eng, _team_ops, _proj_demo, _proj_tools, apps = _scaffold()
    user = _user("teamview-perms")
    role = _role("team-deployer-perms", ["app.read", "app.deploy"])
    RoleBinding.objects.create(user=user, role=role, scope_kind="TEAM", scope_id=team_eng.id)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        assert _app_perms("alpha-perms") == ["app.deploy", "app.read"]
        assert _app_perms("beta-perms") == ["app.deploy", "app.read"]
        # gamma lives under team_ops — no TEAM/ORG/PROJECT/APP match.
        assert _app_perms("gamma-perms") == []


def test_project_binding_grants_only_under_that_project():
    """PROJECT-scoped binding constrains visibility to the project's
    apps even when the team owns multiple projects."""
    org, team_eng, _team_ops, project_demo, _project_tools, apps = _scaffold()
    # Extra project under the same team; the project binding must NOT
    # reach an app sitting in this sibling project.
    other_project = Project.objects.create(organization=org, team=team_eng, name="Other", slug="other-perms")
    apps["delta"] = RegisteredApp.objects.create(
        organization=org, team=team_eng, project=other_project, name="Delta", slug="delta-perms"
    )
    user = _user("projview-perms")
    role = _role("project-reader-perms", ["app.read"])
    RoleBinding.objects.create(user=user, role=role, scope_kind="PROJECT", scope_id=project_demo.id)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        assert _app_perms("alpha-perms") == ["app.read"]
        assert _app_perms("beta-perms") == ["app.read"]
        # Sibling project under the same team — not reached by the
        # PROJECT binding on project_demo.
        assert _app_perms("delta-perms") == []
        # Other team / project entirely — empty.
        assert _app_perms("gamma-perms") == []


def test_app_direct_binding_grants_only_that_app():
    """APP-scoped binding lights up exactly one row; siblings stay
    empty even if they live in the same team / project."""
    org, *_rest, apps = _scaffold()
    user = _user("appview-perms")
    role = _role("app-only-perms", ["app.read", "app.deploy"])
    RoleBinding.objects.create(user=user, role=role, scope_kind="APP", scope_id=apps["beta"].id)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        assert _app_perms("beta-perms") == ["app.deploy", "app.read"]
        # alpha sits in the same project/team but the APP binding is
        # the only one — so alpha returns empty.
        assert _app_perms("alpha-perms") == []
        assert _app_perms("gamma-perms") == []


def test_no_binding_returns_empty_not_error():
    """An authed viewer with zero bindings gets ``[]`` for any app they
    can name — the FE relies on the empty list to disable every action,
    so it must NOT raise."""
    org, *_rest, apps = _scaffold()
    user = _user("nobody-perms")
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        assert _app_perms("alpha-perms") == []
        # Unknown slug also returns empty (no leakage about whether
        # the slug exists in another tenant).
        assert _app_perms("does-not-exist") == []


def test_expired_binding_does_not_grant():
    """A RoleBinding whose ``expires_at`` is in the past is dormant —
    the time-boxed grant rule lives in the resolver, mirroring the
    @require_permission path."""
    org, *_rest, apps = _scaffold()
    user = _user("expired-perms")
    role = _role("expired-role-perms", ["app.deploy"])
    RoleBinding.objects.create(
        user=user,
        role=role,
        scope_kind="APP",
        scope_id=apps["alpha"].id,
        expires_at=timezone.now() - timedelta(hours=1),
    )
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        assert _app_perms("alpha-perms") == []


def test_cross_org_binding_does_not_leak_across_tenant():
    """A binding in org B never shows up when the active tenant is org
    A, even if the app slug coincidentally matches. The query also
    hides apps that live in a different org so the slug existence
    can't be probed cross-tenant."""
    org_a, *_rest, _apps_a = _scaffold()
    org_b = Organization.objects.create(name="Other", slug="other-perms-org")
    team_b = Team.objects.create(organization=org_b, name="Core", slug="core-perms")
    project_b = Project.objects.create(organization=org_b, team=team_b, name="Misc", slug="misc-perms")
    RegisteredApp.objects.create(
        organization=org_b, team=team_b, project=project_b, name="Delta", slug="delta-other-perms"
    )

    user = _user("xtenant-perms")
    role = _role("xtenant-role-perms", ["app.deploy"])
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org_b.id)

    # Acting in org A — the org B binding doesn't surface on the org A
    # app, and the org B app slug is invisible (returns []).
    with tenant_context(TenantContext(organization_id=org_a.id, actor_user_id=user.id)):
        assert _app_perms("alpha-perms") == []
        assert _app_perms("delta-other-perms") == []
    # Flip tenant context to org B and the binding lights up on its
    # own app — sanity check that the binding itself is intact.
    with tenant_context(TenantContext(organization_id=org_b.id, actor_user_id=user.id)):
        assert _app_perms("delta-other-perms") == ["app.deploy"]


# ---------- viewerPermissions on AstroliftRegisteredApp -----------------


def test_apps_list_viewer_permissions_query_count_does_not_scale_with_app_count():
    """The ``viewerPermissions`` field on the apps-list resolver must
    use ONE bulk RoleBinding fetch regardless of the row count — that's
    the dataloader contract #478 calls out.

    Measurement strategy: count the RoleBinding-touching queries on a
    1-app tenant and a 3-app tenant. The viewer-permissions resolver
    layer must produce the same number of RB queries in both runs.
    (Other RB queries exist — notably the @require_permission gate on
    the resolver entry, which runs once per request — and those stay
    the same when app count changes, which is the whole point of the
    test.)
    """
    org, team_eng, _team_ops, _project_demo, _project_tools, apps = _scaffold()
    user = _user("nplus1-viewer-perms")
    org_role = _role("nplus1-org-role-perms", ["app.read"])
    app_role = _role("nplus1-app-role-perms", ["app.deploy"])
    RoleBinding.objects.create(user=user, role=org_role, scope_kind="ORG", scope_id=org.id)
    RoleBinding.objects.create(user=user, role=app_role, scope_kind="APP", scope_id=apps["alpha"].id)

    # Soft-delete two of three apps for the 1-app baseline; restore for
    # the 3-app run. Avoids spawning a separate tenant which would also
    # change other query counts (org / team / project lookups).
    apps["beta"].soft_delete()
    apps["gamma"].soft_delete()

    q = RegistryQuery()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        # Warm caches so the first call isn't paying for any one-time
        # Django table-name lookups that would skew the delta.
        q.astrolift_apps(_info())
        with CaptureQueriesContext(connection) as ctx_1:
            results_1 = q.astrolift_apps(_info())
        # Bring the other apps back for the 3-app measurement.
        for slug in ("beta", "gamma"):
            apps[slug].deleted_at = None
            apps[slug].save(update_fields=["deleted_at"])
        with CaptureQueriesContext(connection) as ctx_3:
            results_3 = q.astrolift_apps(_info())

    # The list IS returning more rows in the second pass — sanity check
    # the resolver actually saw the extra apps before reasoning about
    # query counts.
    assert len(results_3) > len(results_1)

    rb_q_1 = sum(1 for q_ in ctx_1.captured_queries if 'FROM "astrolift_identity_rolebinding"' in q_["sql"])
    rb_q_3 = sum(1 for q_ in ctx_3.captured_queries if 'FROM "astrolift_identity_rolebinding"' in q_["sql"])
    # Identical RB query count regardless of result-row count. THIS is
    # the N+1 guard — if the per-row serialiser ever spawns a query
    # per app, this delta will jump in lockstep with the row count.
    assert rb_q_1 == rb_q_3, (
        f"viewerPermissions field is N+1: {rb_q_1} RB queries for {len(results_1)} apps vs "
        f"{rb_q_3} for {len(results_3)} apps"
    )

    by_slug = {a.slug: a for a in results_3}
    # alpha picks up both bindings (ORG + APP); beta / gamma only
    # carry the ORG binding's slugs.
    assert by_slug["alpha-perms"].viewer_permissions == ["app.deploy", "app.read"]
    assert by_slug["beta-perms"].viewer_permissions == ["app.read"]
    assert by_slug["gamma-perms"].viewer_permissions == ["app.read"]


def test_viewer_permissions_field_matches_app_permissions_query():
    """The ``viewerPermissions`` field on the list resolver must agree
    with ``astroliftAppPermissions`` on the same app — they're two
    surfaces over the same logic, so the contract is "identical
    output". A drift here is the bug class #478 is meant to prevent.

    Layered bindings: an ORG-scope reader role (covers the gate on
    ``astroliftApps`` and surfaces a baseline ``app.read`` on every
    row) plus an APP-scope deployer role on alpha only (additive
    ``app.deploy`` on that one row). The field on the list call must
    union both for alpha and emit just the org row for gamma, matching
    what the per-app resolver returns on its own.
    """
    org, _team_eng, _team_ops, _project_demo, _project_tools, apps = _scaffold()
    user = _user("agree-viewer-perms")
    org_role = _role("agree-org-role-perms", ["app.read"])
    app_role = _role("agree-app-role-perms", ["app.deploy"])
    RoleBinding.objects.create(user=user, role=org_role, scope_kind="ORG", scope_id=org.id)
    RoleBinding.objects.create(user=user, role=app_role, scope_kind="APP", scope_id=apps["alpha"].id)

    q = RegistryQuery()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        list_result = {a.slug: a.viewer_permissions for a in q.astrolift_apps(_info())}
        single_alpha = _app_perms("alpha-perms")
        single_gamma = _app_perms("gamma-perms")

    # alpha picks up both bindings; gamma sees only the ORG reader.
    assert list_result["alpha-perms"] == single_alpha == ["app.deploy", "app.read"]
    assert list_result["gamma-perms"] == single_gamma == ["app.read"]
