"""permissionDiagnose answers about the real permission system (#1730).

It used to 500 on every user/permission pair -- `'NoneType' object has no
attribute 'name'`, from dereferencing a null organization in the step
that reported group membership. That was the smaller half.

The larger half: every resolver in the module was written against
`django.contrib.auth` Group / Permission. Astrolift does not authorize on
Django groups; it authorizes on RoleBinding -> Role.permissions, resolved
by `permission_resolver.resolve`. So `permission` was matched against
`auth_permission.codename` and never against the slugs the gates read,
and `granted` came out of group membership -- free to report denied for a
permission the caller demonstrably holds.

The property that makes this trustworthy, and the one asserted hardest
below: `granted` is whatever `resolve()` returns for the same tenant
context, because it is computed by calling it. A diagnostic that can
disagree with the gate it explains is worse than no diagnostic.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization, Role, RoleBinding, Team
from astrolift_identity.permission_resolver import resolve
from core.permissions import Permission
from core.schema.types.permission_analysis import PermissionAnalysisQuery
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


def _user(handle, **kw):
    User = get_user_model()
    return User.objects.create(username=handle, email=f"{handle}@test", **kw)


def _info(caller):
    return SimpleNamespace(context=SimpleNamespace(user=caller, request=SimpleNamespace(user=caller)))


@pytest.fixture
def world():
    org = Organization.objects.create(name="Acme", slug="acme-1730")
    team = Team.objects.create(organization=org, name="MedOps", slug="medops-1730")
    return SimpleNamespace(org=org, team=team)


def _bind(user, org, *, permissions, kind, scope_id, slug):
    role = Role.objects.create(
        name=slug,
        slug=slug,
        scope_level=getattr(Role.ScopeLevel, kind),
        permissions=[p.value for p in permissions],
        is_system=False,
    )
    return RoleBinding.objects.create(user=user, role=role, scope_kind=kind, scope_id=scope_id)


def _diagnose(caller, target, permission, org_id):
    with tenant_context(TenantContext(organization_id=org_id, actor_user_id=caller.pk)):
        return PermissionAnalysisQuery().permission_diagnose(
            _info(caller), user_id=str(target.pk), permission=permission
        )


def _checks(diagnosis):
    return {s.check: s for s in diagnosis.steps}


# ---- the crash --------------------------------------------------------


def test_a_user_with_no_organization_gets_a_diagnosis_not_a_500(world):
    """The reported failure: every pair 500'd on a null organization."""

    reba = _user("reba-1730")

    result = _diagnose(reba, reba, "team.read", None)

    assert result is not None
    assert result.granted is False
    assert _checks(result)["has_active_organization"].result is False


# ---- it answers about the right system --------------------------------


def test_a_team_scoped_grant_is_traced_to_its_binding(world):
    reba = _user("reba-bound-1730")
    _bind(
        reba,
        world.org,
        permissions=[Permission.TEAM_READ],
        kind="TEAM",
        scope_id=world.team.id,
        slug="td-1730",
    )

    result = _diagnose(reba, reba, "team.read", world.org.id)

    steps = _checks(result)
    assert steps["role_bindings_in_this_org"].result is True
    assert "td-1730@TEAM" in steps["role_bindings_in_this_org"].detail
    assert steps["bindings_carrying_this_permission"].result is True


def test_a_django_codename_is_named_as_the_wrong_system(world):
    """`view_team` is a Django codename. The old code looked it up in
    auth_permission and reported on it; there is nothing to report."""

    reba = _user("reba-codename-1730")

    result = _diagnose(reba, reba, "view_team", world.org.id)

    step = _checks(result)["permission_is_declared"]
    assert step.result is False
    assert "catalog" in step.detail
    assert result.granted is False


def test_a_superuser_short_circuits_the_way_the_resolver_does(world):
    root = _user("root-1730", is_superuser=True)

    result = _diagnose(root, root, "app.deploy", world.org.id)

    assert result.granted is True
    assert result.is_superuser is True
    assert _checks(result)["is_superuser"].result is True


def test_an_inactive_user_is_denied_before_anything_else(world):
    ghost = _user("ghost-1730", is_active=False, is_superuser=True)

    result = _diagnose(ghost, ghost, "app.deploy", world.org.id)

    assert result.granted is False
    assert _checks(result)["is_active"].result is False


# ---- the invariant ----------------------------------------------------


@pytest.mark.parametrize(
    "permission",
    [Permission.TEAM_READ, Permission.APP_DEPLOY, Permission.ORG_DELETE],
)
def test_the_diagnosis_never_disagrees_with_the_gate(world, permission):
    """The whole point. Whatever `resolve()` says, this says -- including
    for `team.read`, which a TEAM-scoped binding does NOT satisfy at org
    scope. That denial is correct and is the #1717 shape."""

    reba = _user(f"reba-agree-{permission.value.replace('.', '-')}")
    _bind(
        reba,
        world.org,
        permissions=[Permission.TEAM_READ],
        kind="TEAM",
        scope_id=world.team.id,
        slug=f"agree-{permission.value.replace('.', '-')}",
    )

    tenant = TenantContext(organization_id=world.org.id, actor_user_id=reba.pk)
    gate_granted, _reason = resolve(tenant, permission, None)
    diagnosis = _diagnose(reba, reba, permission.value, world.org.id)

    assert diagnosis.granted is gate_granted


def test_a_denial_at_org_scope_names_the_scope_she_does_hold_it_at(world):
    """ "I have the role, why am I denied" is the question this tool
    exists for, and the #1717 shape is its most common cause."""

    reba = _user("reba-scopehint-1730")
    _bind(
        reba,
        world.org,
        permissions=[Permission.TEAM_READ],
        kind="TEAM",
        scope_id=world.team.id,
        slug="hint-1730",
    )

    result = _diagnose(reba, reba, "team.read", world.org.id)

    assert result.granted is False
    verdict = _checks(result)["resolver_verdict"].detail
    assert f"hint-1730@TEAM:{world.team.id}" in verdict
    assert "organization scope" in verdict


def test_a_denial_with_no_binding_does_not_claim_she_holds_it(world):
    reba = _user("reba-nohint-1730")

    result = _diagnose(reba, reba, "team.read", world.org.id)

    assert result.granted is False
    assert "Held at" not in _checks(result)["resolver_verdict"].detail


# ---- effectivePermissions ---------------------------------------------


def test_effective_permissions_lists_slugs_with_their_bindings(world):
    reba = _user("reba-eff-1730")
    _bind(
        reba,
        world.org,
        permissions=[Permission.TEAM_READ, Permission.APP_READ],
        kind="TEAM",
        scope_id=world.team.id,
        slug="eff-1730",
    )

    with tenant_context(TenantContext(organization_id=world.org.id, actor_user_id=reba.pk)):
        entries = PermissionAnalysisQuery().effective_permissions(_info(reba), user_id=str(reba.pk))

    by_slug = {e.slug: e for e in entries}
    assert set(by_slug) == {"team.read", "app.read"}
    assert by_slug["team.read"].resource == "team"
    assert by_slug["team.read"].action == "read"
    assert by_slug["team.read"].granted_via == [f"eff-1730@TEAM:{world.team.id}"]


def test_effective_permissions_excludes_another_orgs_bindings(world):
    other = Organization.objects.create(name="Other", slug="other-1730")
    other_team = Team.objects.create(organization=other, name="T", slug="t-1730")
    reba = _user("reba-crossorg-1730")
    _bind(
        reba,
        other,
        permissions=[Permission.APP_DEPLOY],
        kind="TEAM",
        scope_id=other_team.id,
        slug="cross-1730",
    )

    with tenant_context(TenantContext(organization_id=world.org.id, actor_user_id=reba.pk)):
        entries = PermissionAnalysisQuery().effective_permissions(_info(reba), user_id=str(reba.pk))

    assert entries == []


# ---- permissionCompare ------------------------------------------------


def _compare(caller, a, b, org_id):
    with tenant_context(TenantContext(organization_id=org_id, actor_user_id=caller.pk)):
        return PermissionAnalysisQuery().permission_compare(
            _info(caller), user_id_a=str(a.pk), user_id_b=str(b.pk)
        )


def test_compare_diffs_two_users_slug_sets(world):
    root = _user("root-cmp-1730", is_superuser=True)
    a = _user("a-cmp-1730")
    b = _user("b-cmp-1730")
    _bind(
        a,
        world.org,
        permissions=[Permission.TEAM_READ, Permission.APP_READ],
        kind="TEAM",
        scope_id=world.team.id,
        slug="cmp-a-1730",
    )
    _bind(
        b,
        world.org,
        permissions=[Permission.APP_READ],
        kind="TEAM",
        scope_id=world.team.id,
        slug="cmp-b-1730",
    )

    result = _compare(root, a, b, world.org.id)

    assert result.only_a == ["team.read"]
    assert result.only_b == []
    assert result.shared == ["app.read"]
    assert [(d.slug, d.user_a_has, d.user_b_has) for d in result.differences] == [("team.read", True, False)]


def test_compare_is_superuser_only(world):
    a = _user("a-cmpgate-1730")
    b = _user("b-cmpgate-1730")

    from graphql import GraphQLError

    with pytest.raises(GraphQLError, match="superuser"):
        _compare(a, a, b, world.org.id)


def test_compare_returns_null_for_an_unknown_user(world):
    root = _user("root-cmpnull-1730", is_superuser=True)
    a = _user("a-cmpnull-1730")

    with tenant_context(TenantContext(organization_id=world.org.id, actor_user_id=root.pk)):
        result = PermissionAnalysisQuery().permission_compare(
            _info(root), user_id_a=str(a.pk), user_id_b="999999999"
        )
    assert result is None


def test_diagnose_returns_null_for_an_unknown_user(world):
    root = _user("root-diagnull-1730", is_superuser=True)

    assert _diagnose(root, SimpleNamespace(pk=999999999), "team.read", world.org.id) is None
