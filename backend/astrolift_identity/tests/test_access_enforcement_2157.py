"""The access paths #2157 found configurable but not enforced.

* ABAC policies are evaluated after RBAC and can only deny.
* Group role bindings and GroupRoleMappings grant to the members the IdP
  put in the group, inside the org whose membership row records it.
* AppTeamAccess team shares reach the resolver, not only the agent lists.
* ``RoleBinding.inherits=False`` grants at its own scope only.

Everything runs through the real resolver against real rows.
"""

from __future__ import annotations

import datetime as dt
import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from astrolift_identity import abac
from astrolift_identity.abac import RequestAttributes, operation_attributes, request_attributes
from astrolift_identity.idp_groups import groups_from_claims, member_groups, sync_member_groups
from astrolift_identity.models import GroupRoleMapping, Member, Policy, Project, Role, RoleBinding, Team
from astrolift_identity.permission_resolver import (
    decide,
    granted_scopes,
    resolve,
    resolve_effective_permissions,
    resolve_effective_permissions_anywhere,
    resolve_effective_permissions_for_apps,
)
from astrolift_identity.scope_visibility import visible_projects, visible_teams
from astrolift_registry.models import AppTeamAccess
from core.permissions import NO_SCOPES, Permission, PermissionScope, ScopeKind
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, make_user

pytestmark = pytest.mark.django_db

DEPLOY = Permission.APP_DEPLOY
READ = Permission.APP_READ


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, g: None))


def _tag() -> str:
    return uuid.uuid4().hex[:6]


@pytest.fixture
def world():
    return ScopeWorld(f"2157-{_tag()}")


@pytest.fixture
def other():
    return ScopeWorld(f"2157o-{_tag()}")


def _user(prefix="u"):
    return make_user(f"{prefix}-{_tag()}")


def _member(world, user, groups=()):
    Member.objects.create(
        user=user,
        scope_kind="ORG",
        scope_id=world.org.pk,
        is_active=True,
        lifecycle="active",
        idp_groups=list(groups),
    )
    return user


def _role(*perms, slug=None, org=None):
    return Role.objects.create(
        name=slug or "r",
        slug=slug or f"r-{_tag()}",
        scope_level="ORG",
        permissions=[p.value for p in perms],
        is_system=False,
        organization=org,
    )


def _bind(user, role, kind, scope_id, **extra):
    return RoleBinding.objects.create(user=user, role=role, scope_kind=kind, scope_id=scope_id, **extra)


def _bind_group(group, role, kind, scope_id, **extra):
    return RoleBinding.objects.create(
        group_external_id=group, role=role, scope_kind=kind, scope_id=scope_id, **extra
    )


def _policy(org, *, effect="DENY", action="app.deploy", conditions=(), resource=None, actor=None, **extra):
    return Policy.objects.create(
        organization=org,
        name="p",
        slug=f"p-{_tag()}",
        scope_level=extra.pop("scope_level", "ORG"),
        scope_id=extra.pop("scope_id", None),
        effect=effect,
        action_pattern=action,
        resource_pattern=resource or {},
        conditions=list(conditions),
        actor_pattern=actor or {},
        **extra,
    )


def _tenant(world, user, **selected):
    return TenantContext(organization_id=world.org.pk, actor_user_id=user.pk, **selected)


def _app_scope(app):
    return PermissionScope(kind=ScopeKind.APP, id=app.pk)


def _at(user, *, now=None, ip=None, **extra):
    """Request attributes for ``user``'s own request."""
    return request_attributes(RequestAttributes(actor_user_id=user.pk, now=now, client_ip=ip, **extra))


# A Wednesday, 12:00 UTC.
NOON_WED = dt.datetime(2026, 9, 30, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def deployer(world):
    user = _member(world, _user("dep"))
    _bind(user, _role(DEPLOY, READ), "ORG", world.org.pk)
    return user


# ---------------------------------------------------------------------
# ABAC
# ---------------------------------------------------------------------


def test_a_deny_policy_blocks_an_action_rbac_grants(world, deployer):
    tenant = _tenant(world, deployer)
    assert resolve(tenant, DEPLOY, _app_scope(world.medops_app))[0] is True

    policy = _policy(world.org)

    granted, reason = resolve(tenant, DEPLOY, _app_scope(world.medops_app))
    assert granted is False
    assert policy.slug in reason
    # Another action is untouched.
    assert resolve(tenant, READ, _app_scope(world.medops_app))[0] is True


def test_abac_never_grants_beyond_rbac(world):
    nobody = _member(world, _user("nobody"))
    _policy(world.org, effect="ALLOW", action="*")
    assert resolve(_tenant(world, nobody), DEPLOY, _app_scope(world.medops_app))[0] is False


def test_an_allow_policy_with_no_conditions_changes_nothing(world, deployer):
    _policy(world.org, effect="ALLOW")
    assert resolve(_tenant(world, deployer), DEPLOY, _app_scope(world.medops_app))[0] is True


@pytest.mark.parametrize("effect", ["DENY", "ALLOW"])
def test_a_time_window_decides_both_effects(world, deployer, effect):
    window = {"kind": "time_window", "days": ["mon", "tue", "wed", "thu", "fri"], "hours": ["09:00-18:00"]}
    _policy(world.org, effect=effect, conditions=[{**window, "tz": "UTC"}])
    scope = _app_scope(world.medops_app)
    tenant = _tenant(world, deployer)

    with _at(deployer, now=NOON_WED):
        assert resolve(tenant, DEPLOY, scope)[0] is True
    with _at(deployer, now=NOON_WED.replace(hour=20)):
        granted, reason = resolve(tenant, DEPLOY, scope)
        assert granted is False
        assert "time_window" in reason


def test_an_overnight_window_spans_midnight_from_the_listed_day(world, deployer):
    _policy(
        world.org,
        conditions=[{"kind": "time_window", "days": ["wed"], "hours": ["22:00-06:00"], "tz": "UTC"}],
    )
    tenant, scope = _tenant(world, deployer), _app_scope(world.medops_app)
    with _at(deployer, now=NOON_WED.replace(hour=23)):
        assert resolve(tenant, DEPLOY, scope)[0] is True
    with _at(deployer, now=NOON_WED + timedelta(hours=15)):  # Thu 03:00
        assert resolve(tenant, DEPLOY, scope)[0] is True
    with _at(deployer, now=NOON_WED - timedelta(hours=9)):  # Wed 03:00, window opened Tue
        assert resolve(tenant, DEPLOY, scope)[0] is False


def test_a_window_in_a_named_zone_reads_local_time(world, deployer):
    # 12:00 UTC is 05:00 in Los Angeles: outside 09:00-18:00 there.
    _policy(
        world.org,
        conditions=[
            {"kind": "time_window", "days": ["wed"], "hours": ["09:00-18:00"], "tz": "America/Los_Angeles"}
        ],
    )
    with _at(deployer, now=NOON_WED):
        assert resolve(_tenant(world, deployer), DEPLOY, _app_scope(world.medops_app))[0] is False


@pytest.mark.parametrize(
    ("condition", "missing"),
    [
        ({"kind": "ip_allowlist", "cidrs": ["10.0.0.0/8"]}, "no client IP"),
        ({"kind": "freshness", "max_session_age_minutes": 15}, "no sign-in time"),
        ({"kind": "device_assertion", "required_factors": ["webauthn"]}, "no signed-in session"),
        ({"kind": "approval_required", "min_approvers": 2}, "no approval count"),
        ({"kind": "env_match", "env_in": ["staging"]}, "no environment"),
        ({"kind": "geo_fence", "countries": ["US"]}, "unknown condition kind"),
        ({"kind": "time_window", "days": ["mon"], "hours": ["9-5"], "tz": "UTC"}, "malformed"),
        (
            {"kind": "time_window", "days": ["mon"], "hours": ["09:00-17:00"], "tz": "Mars/Base"},
            "unknown time zone",
        ),
        ("not an object", "not an object"),
    ],
)
def test_a_condition_that_cannot_be_evaluated_denies_and_says_why(world, deployer, condition, missing):
    _policy(world.org, conditions=[condition])
    with _at(deployer, now=NOON_WED):
        granted, reason = resolve(_tenant(world, deployer), DEPLOY, _app_scope(world.medops_app))
    assert granted is False
    assert "cannot be evaluated" in reason or "malformed" in reason
    assert missing in reason


def test_an_ip_allowlist_reads_the_requests_ip(world, deployer):
    _policy(world.org, conditions=[{"kind": "ip_allowlist", "cidrs": ["10.0.0.0/8"]}])
    tenant, scope = _tenant(world, deployer), _app_scope(world.medops_app)
    with _at(deployer, ip="10.1.2.3"):
        assert resolve(tenant, DEPLOY, scope)[0] is True
    with _at(deployer, ip="192.0.2.7"):
        assert resolve(tenant, DEPLOY, scope)[0] is False


def test_person_bound_attributes_never_describe_someone_else(world, deployer):
    """The request's IP is its own user's. A check about anyone else (a
    diagnosis) finds it unavailable, and fails closed."""
    _policy(world.org, conditions=[{"kind": "ip_allowlist", "cidrs": ["10.0.0.0/8"]}])
    admin = _member(world, _user("admin"))
    with _at(admin, ip="10.1.2.3"):
        granted, reason = resolve(_tenant(world, deployer), DEPLOY, _app_scope(world.medops_app))
    assert granted is False
    assert "no client IP" in reason


def test_freshness_and_factors_read_the_session(world, deployer):
    _policy(
        world.org,
        conditions=[
            {"kind": "freshness", "max_session_age_minutes": 15},
            {"kind": "device_assertion", "required_factors": ["sso"]},
        ],
    )
    tenant, scope = _tenant(world, deployer), _app_scope(world.medops_app)
    fresh = RequestAttributes(
        actor_user_id=deployer.pk,
        now=NOON_WED,
        authenticated_at=NOON_WED - timedelta(minutes=5),
        auth_factors=frozenset({"sso"}),
    )
    with request_attributes(fresh):
        assert resolve(tenant, DEPLOY, scope)[0] is True
    stale = RequestAttributes(
        actor_user_id=deployer.pk,
        now=NOON_WED,
        authenticated_at=NOON_WED - timedelta(minutes=50),
        auth_factors=frozenset({"sso"}),
    )
    with request_attributes(stale):
        assert resolve(tenant, DEPLOY, scope)[0] is False


def test_an_env_pattern_fails_closed_until_the_call_site_names_the_environment(world, deployer):
    _policy(world.org, resource={"env": "production"})
    tenant, scope = _tenant(world, deployer), _app_scope(world.medops_app)

    granted, reason = resolve(tenant, DEPLOY, scope)
    assert granted is False
    assert "no environment" in reason
    with operation_attributes(environment="staging"):
        assert resolve(tenant, DEPLOY, scope)[0] is True
    with operation_attributes(environment="production"):
        assert resolve(tenant, DEPLOY, scope)[0] is False


def test_resource_and_scope_patterns_confine_a_policy(world, deployer):
    _policy(world.org, resource={"app_slug": world.medops_app.slug})
    _policy(world.org, action="app.read", scope_level="TEAM", scope_id=world.platform.pk)
    tenant = _tenant(world, deployer)

    assert resolve(tenant, DEPLOY, _app_scope(world.medops_app))[0] is False
    assert resolve(tenant, DEPLOY, _app_scope(world.platform_app))[0] is True
    # A team target is in no app: an app_slug policy does not match it.
    assert resolve(tenant, DEPLOY, PermissionScope(kind=ScopeKind.TEAM, id=world.medops.pk))[0] is True
    assert resolve(tenant, READ, _app_scope(world.platform_app))[0] is False
    assert resolve(tenant, READ, _app_scope(world.medops_app))[0] is True


def test_actor_patterns_pick_who_a_policy_covers(world):
    ops = _member(world, _user("ops"), groups=["contractors"])
    staff = _member(world, _user("staff"))
    role = _role(DEPLOY)
    for user in (ops, staff):
        _bind(user, role, "ORG", world.org.pk)
    _policy(world.org, actor={"user_in_groups": ["contractors"]})
    scope = _app_scope(world.medops_app)

    assert resolve(_tenant(world, ops), DEPLOY, scope)[0] is False
    assert resolve(_tenant(world, staff), DEPLOY, scope)[0] is True


def test_a_policy_of_another_org_never_applies(world, other, deployer):
    _policy(other.org)
    assert resolve(_tenant(world, deployer), DEPLOY, _app_scope(world.medops_app))[0] is True


def test_the_superuser_is_not_a_principal_of_org_policies(world):
    root = _user("root")
    root.is_superuser = True
    root.save()
    _policy(world.org)
    assert resolve(_tenant(world, root), DEPLOY, _app_scope(world.medops_app)) == (True, "django superuser")


def test_derived_permission_sets_drop_what_a_policy_denies(world, deployer):
    tenant = _tenant(world, deployer)
    _policy(world.org)

    assert DEPLOY.value not in resolve_effective_permissions(tenant, extra_scope=("APP", world.medops_app.pk))
    bulk = resolve_effective_permissions_for_apps(tenant, [world.medops_app, world.platform_app])
    assert all(DEPLOY.value not in perms and READ.value in perms for perms in bulk.values())
    assert DEPLOY.value not in resolve_effective_permissions_anywhere(tenant)
    assert granted_scopes(tenant, DEPLOY) == NO_SCOPES
    assert granted_scopes(tenant, READ).org is True


def test_a_narrow_policy_does_not_empty_the_any_scope_answer(world, deployer):
    """Only a policy covering the whole org can deny "anywhere"; a narrow
    one still denies at the object gate."""
    _policy(world.org, resource={"app_slug": world.medops_app.slug})
    tenant = _tenant(world, deployer)
    assert granted_scopes(tenant, DEPLOY).org is True
    assert resolve(tenant, DEPLOY, _app_scope(world.medops_app))[0] is False


def test_policies_are_cached_per_request_not_across_requests(world, deployer):
    tenant, scope = _tenant(world, deployer), _app_scope(world.medops_app)
    first = RequestAttributes(actor_user_id=deployer.pk)
    with request_attributes(first):
        assert resolve(tenant, DEPLOY, scope)[0] is True
        _policy(world.org)
        # Same request: the policy set was read once.
        assert resolve(tenant, DEPLOY, scope)[0] is True
        with CaptureQueriesContext(connection) as queries:
            resolve(tenant, DEPLOY, scope)
        assert not any("astrolift_identity_policy" in q["sql"] for q in queries.captured_queries)
    with request_attributes(RequestAttributes(actor_user_id=deployer.pk)):
        # The next request sees it.
        assert resolve(tenant, DEPLOY, scope)[0] is False


def test_the_decision_carries_the_policy_outcomes(world, deployer):
    policy = _policy(world.org, conditions=[{"kind": "ip_allowlist", "cidrs": ["10.0.0.0/8"]}])
    decision = decide(_tenant(world, deployer), DEPLOY, _app_scope(world.medops_app))
    assert decision.rbac_granted is True
    assert decision.granted is False
    (outcome,) = decision.abac.applied
    assert outcome.policy_slug == policy.slug
    assert outcome.conditions[0].holds is None


def test_the_evaluator_reads_the_frontend_shapes():
    """Every condition kind policy-model.ts writes is known here."""
    kinds = {"time_window", "ip_allowlist", "approval_required", "env_match", "device_assertion", "freshness"}
    assert kinds == set(abac._HANDLERS)


# ---------------------------------------------------------------------
# IdP groups
# ---------------------------------------------------------------------


def test_groups_come_from_the_standard_claims():
    assert groups_from_claims({"groups": ["b", "a", "a", 3, ""]}) == ["a", "b"]
    assert groups_from_claims({"cognito:groups": ["ops"]}) == ["ops"]
    assert groups_from_claims({"groups": "solo"}) == ["solo"]
    assert groups_from_claims({"email": "x"}) == []


def test_sign_in_replaces_the_groups_on_every_org_membership(world, other):
    user = _user("sso")
    _member(world, user, groups=["stale"])
    _member(other, user)

    assert sync_member_groups(user, {"groups": ["eng"]}) == 2
    assert member_groups(user.pk, world.org.pk) == {"eng"}
    assert member_groups(user.pk, other.org.pk) == {"eng"}

    sync_member_groups(user, {})
    assert member_groups(user.pk, world.org.pk) == frozenset()


def test_a_group_binding_grants_to_members_of_the_group_only(world):
    role = _role(DEPLOY)
    _bind_group("eng", role, "TEAM", world.medops.pk)
    inside = _member(world, _user("in"), groups=["eng"])
    outside = _member(world, _user("out"), groups=["sales"])
    scope = _app_scope(world.medops_app)

    granted, reason = resolve(_tenant(world, inside), DEPLOY, scope)
    assert granted is True
    assert "via group eng" in reason
    assert resolve(_tenant(world, outside), DEPLOY, scope)[0] is False
    assert resolve(_tenant(world, inside), DEPLOY, _app_scope(world.platform_app))[0] is False


def test_a_group_recorded_in_another_org_grants_nothing_here(world, other):
    user = _user("x")
    _member(world, user)
    _member(other, user, groups=["eng"])
    _bind_group("eng", _role(DEPLOY), "ORG", world.org.pk)
    assert resolve(_tenant(world, user), DEPLOY, _app_scope(world.medops_app))[0] is False


def test_a_group_binding_in_another_org_grants_nothing_here(world, other):
    user = _member(world, _user("y"), groups=["eng"])
    _bind_group("eng", _role(DEPLOY), "ORG", other.org.pk)
    assert resolve(_tenant(world, user), DEPLOY, _app_scope(world.medops_app))[0] is False


def test_an_expired_group_binding_grants_nothing(world):
    user = _member(world, _user("z"), groups=["eng"])
    _bind_group("eng", _role(DEPLOY), "ORG", world.org.pk, expires_at=timezone.now() - timedelta(minutes=1))
    assert resolve(_tenant(world, user), DEPLOY, _app_scope(world.medops_app))[0] is False


def test_a_group_mapping_applies_as_a_group_binding(world, other):
    user = _member(world, _user("m"), groups=["eng"])
    stranger = _member(world, _user("s"))
    mapping = GroupRoleMapping.objects.create(
        organization=world.org,
        group_external_id="eng",
        role=_role(DEPLOY),
        scope_kind="PROJECT",
        scope_id=world.medops_project.pk,
    )
    granted, reason = resolve(_tenant(world, user), DEPLOY, _app_scope(world.medops_app))
    assert granted is True
    assert "via group mapping eng" in reason
    assert resolve(_tenant(world, stranger), DEPLOY, _app_scope(world.medops_app))[0] is False
    assert DEPLOY.value in resolve_effective_permissions_anywhere(_tenant(world, user))

    mapping.soft_delete()
    assert resolve(_tenant(world, user), DEPLOY, _app_scope(world.medops_app))[0] is False


def test_another_orgs_group_mapping_grants_nothing_here(world, other):
    user = _member(world, _user("n"), groups=["eng"])
    GroupRoleMapping.objects.create(
        organization=other.org,
        group_external_id="eng",
        role=_role(DEPLOY),
        scope_kind="ORG",
        scope_id=world.org.pk,  # a forged scope id pointing at this org
    )
    assert resolve(_tenant(world, user), DEPLOY, _app_scope(world.medops_app))[0] is False


def test_a_group_mapping_with_another_orgs_role_grants_nothing(world, other):
    user = _member(world, _user("q"), groups=["eng"])
    GroupRoleMapping.objects.create(
        organization=world.org,
        group_external_id="eng",
        role=_role(DEPLOY, org=other.org),
        scope_kind="ORG",
        scope_id=world.org.pk,
    )
    assert resolve(_tenant(world, user), DEPLOY, _app_scope(world.medops_app))[0] is False


# ---------------------------------------------------------------------
# Team shares
# ---------------------------------------------------------------------


def _share(app, team, level):
    return AppTeamAccess.objects.create(registered_app=app, team=team, access_level=level)


def test_a_team_share_grants_on_the_shared_app(world):
    user = _member(world, _user("sh"))
    _bind(user, _role(DEPLOY, READ), "TEAM", world.medops.pk)
    scope = _app_scope(world.platform_app)
    assert resolve(_tenant(world, user), DEPLOY, scope)[0] is False

    _share(world.platform_app, world.medops, "deployer")

    granted, reason = resolve(_tenant(world, user), DEPLOY, scope)
    assert granted is True
    assert "team share (deployer)" in reason
    perms = resolve_effective_permissions(_tenant(world, user), extra_scope=("APP", world.platform_app.pk))
    assert {DEPLOY.value, READ.value} <= perms
    bulk = resolve_effective_permissions_for_apps(_tenant(world, user), [world.platform_app])
    assert DEPLOY.value in bulk[world.platform_app.pk]


def test_a_viewer_share_carries_reads_only(world):
    user = _member(world, _user("v"))
    _bind(user, _role(DEPLOY, READ), "TEAM", world.medops.pk)
    _share(world.platform_app, world.medops, "viewer")
    scope = _app_scope(world.platform_app)

    assert resolve(_tenant(world, user), READ, scope)[0] is True
    assert resolve(_tenant(world, user), DEPLOY, scope)[0] is False
    perms = resolve_effective_permissions(_tenant(world, user), extra_scope=("APP", world.platform_app.pk))
    assert READ.value in perms and DEPLOY.value not in perms


def test_a_share_reaches_group_members_of_the_team(world):
    user = _member(world, _user("g"), groups=["medops"])
    _bind_group("medops", _role(DEPLOY), "TEAM", world.medops.pk)
    _share(world.platform_app, world.medops, "owner")
    assert resolve(_tenant(world, user), DEPLOY, _app_scope(world.platform_app))[0] is True


def test_a_share_from_another_orgs_team_grants_nothing(world, other):
    user = _member(world, _user("f"))
    _bind(user, _role(DEPLOY), "TEAM", other.medops.pk)
    _share(world.platform_app, other.medops, "owner")
    assert resolve(_tenant(world, user), DEPLOY, _app_scope(world.platform_app))[0] is False


def test_a_non_inheriting_team_grant_does_not_travel_through_a_share(world):
    user = _member(world, _user("ni"))
    _bind(user, _role(DEPLOY), "TEAM", world.medops.pk, inherits=False)
    _share(world.platform_app, world.medops, "owner")
    assert resolve(_tenant(world, user), DEPLOY, _app_scope(world.platform_app))[0] is False


def test_a_deleted_share_grants_nothing(world):
    user = _member(world, _user("d"))
    _bind(user, _role(DEPLOY), "TEAM", world.medops.pk)
    _share(world.platform_app, world.medops, "owner").soft_delete()
    assert resolve(_tenant(world, user), DEPLOY, _app_scope(world.platform_app))[0] is False


def test_policies_still_deny_a_shared_grant(world):
    user = _member(world, _user("sp"))
    _bind(user, _role(DEPLOY), "TEAM", world.medops.pk)
    _share(world.platform_app, world.medops, "owner")
    _policy(world.org)
    assert resolve(_tenant(world, user), DEPLOY, _app_scope(world.platform_app))[0] is False


# ---------------------------------------------------------------------
# inherits=False
# ---------------------------------------------------------------------


def test_a_non_inheriting_org_binding_grants_at_the_org_only(world):
    user = _member(world, _user("o"))
    _bind(user, _role(Permission.TEAM_READ, READ), "ORG", world.org.pk, inherits=False)
    tenant = _tenant(world, user)

    assert resolve(tenant, READ, None)[0] is True
    assert resolve(tenant, READ, PermissionScope(kind=ScopeKind.ORG, id=world.org.pk))[0] is True
    assert resolve(tenant, READ, PermissionScope(kind=ScopeKind.TEAM, id=world.medops.pk))[0] is False
    assert resolve(tenant, READ, _app_scope(world.medops_app))[0] is False
    assert resolve_effective_permissions_for_apps(tenant, [world.medops_app])[world.medops_app.pk] == set()
    # A targetless check with a team selected targets the team.
    assert resolve(_tenant(world, user, team_id=world.medops.pk), READ, None)[0] is False
    # Lists get no rows below the org from it.
    scopes = granted_scopes(tenant, Permission.TEAM_READ)
    assert scopes.org is False and not scopes


def test_a_non_inheriting_team_binding_covers_the_team_row_only(world):
    user = _member(world, _user("t"))
    _bind(
        user,
        _role(Permission.TEAM_READ, Permission.PROJECT_READ, READ),
        "TEAM",
        world.medops.pk,
        inherits=False,
    )
    tenant = _tenant(world, user)

    assert resolve(tenant, READ, PermissionScope(kind=ScopeKind.TEAM, id=world.medops.pk))[0] is True
    assert (
        resolve(tenant, READ, PermissionScope(kind=ScopeKind.PROJECT, id=world.medops_project.pk))[0] is False
    )
    assert resolve(tenant, READ, _app_scope(world.medops_app))[0] is False

    with tenant_context(tenant):
        assert list(visible_teams(Team.objects.filter(organization=world.org), Permission.TEAM_READ)) == [
            world.medops
        ]
        assert (
            list(visible_projects(Project.objects.filter(organization=world.org), Permission.PROJECT_READ))
            == []
        )
    scopes = granted_scopes(tenant, Permission.TEAM_READ)
    assert scopes.team_ids == frozenset() and scopes.exact_team_ids == {world.medops.pk}


def test_an_inheriting_binding_still_reaches_descendants(world):
    user = _member(world, _user("i"))
    _bind(user, _role(READ), "TEAM", world.medops.pk)
    assert resolve(_tenant(world, user), READ, _app_scope(world.medops_app))[0] is True


def test_the_bulk_answer_does_not_scale_with_app_count_under_policies(world, deployer):
    _policy(world.org, resource={"app_slug": world.medops_app.slug})
    tenant = _tenant(world, deployer)
    with CaptureQueriesContext(connection) as one:
        resolve_effective_permissions_for_apps(tenant, [world.medops_app])
    with CaptureQueriesContext(connection) as two:
        result = resolve_effective_permissions_for_apps(tenant, [world.medops_app, world.platform_app])
    assert len(one) == len(two)
    assert DEPLOY.value not in result[world.medops_app.pk]
    assert DEPLOY.value in result[world.platform_app.pk]


def test_the_tenant_middleware_scopes_attributes_to_one_request(rf):
    from django.contrib.auth.models import AnonymousUser
    from django.http import HttpResponse

    from core.middleware.tenant import TenantContextMiddleware

    request = rf.get("/", REMOTE_ADDR="10.2.3.4")
    request.user = AnonymousUser()
    request.session = {}
    middleware = TenantContextMiddleware(lambda r: HttpResponse())

    middleware.process_request(request)
    attrs = abac.current_attributes()
    assert attrs is not None and attrs.client_ip == "10.2.3.4" and attrs.cache == {}
    middleware.process_response(request, HttpResponse())
    assert abac.current_attributes() is None


def test_the_allowlist_ip_is_the_one_the_proxy_appended(rf):
    """A client-written X-Forwarded-For entry must not pass an allowlist."""
    forged = rf.get("/", HTTP_X_FORWARDED_FOR="10.0.0.1, 198.51.100.9", REMOTE_ADDR="172.16.0.2")
    assert abac.policy_client_ip(forged) == "198.51.100.9"
    direct = rf.get("/", REMOTE_ADDR="10.9.9.9")
    assert abac.policy_client_ip(direct) == "10.9.9.9"
    junk = rf.get("/", HTTP_X_FORWARDED_FOR="not-an-ip")
    assert abac.policy_client_ip(junk) is None
