"""The access revision moves when a viewer's capabilities may have (#1867).

Clients cache ``astroliftMyPermissions`` and each object's
``viewerPermissions``; ``astroliftAccessRevision`` tells them when to drop
those. It must change on a change to the viewer's roles, attributes,
policies or session, and stay put otherwise so clients do not refetch for
nothing.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from astrolift_identity.abac import RequestAttributes, request_attributes
from astrolift_identity.models import ApiToken, GroupRoleMapping, Member, Policy, RoleBinding
from astrolift_identity.schema.queries import IdentityQuery
from astrolift_registry.models import AppTeamAccess
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_search(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))


@pytest.fixture
def world():
    world = ScopeWorld("rev-1867")
    world.user = make_user("rev-1867")
    world.member = Member.objects.create(
        user=world.user, scope_kind="ORG", scope_id=world.org.pk, idp_groups=["eng"]
    )
    world.binding = bind_role(
        world.user, permissions=(Permission.APP_READ,), kind="ORG", scope_id=world.org.pk, slug="rev-read"
    )
    return world


def revision(world, *, session_key="s1", client_ip="10.0.0.1", authenticated_at=None):
    info = SimpleNamespace(
        context=SimpleNamespace(
            request=SimpleNamespace(user=world.user, session=SimpleNamespace(session_key=session_key))
        )
    )
    attrs = RequestAttributes(
        actor_user_id=world.user.pk,
        client_ip=client_ip,
        authenticated_at=authenticated_at,
        auth_factors=frozenset({"password"}),
    )
    with request_attributes(attrs):
        with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk)):
            return IdentityQuery().astrolift_access_revision(info)


def _grant(world):
    bind_role(
        world.user, permissions=(Permission.APP_DEPLOY,), kind="APP", scope_id=world.medops_app.pk, slug="g"
    )


def _revoke(world):
    world.binding.soft_delete()


def _hard_delete(world):
    RoleBinding.all_objects.filter(pk=world.binding.pk).delete()


def _expire(world):
    # A queryset update skips version and updated_at; the lapse still shows.
    RoleBinding.objects.filter(pk=world.binding.pk).update(expires_at=timezone.now() - timedelta(seconds=1))


def _edit_role(world):
    role = world.binding.role
    role.permissions = [*role.permissions, Permission.APP_DEPLOY.value]
    role.save()


def _group_binding(world):
    RoleBinding.objects.create(
        group_external_id="eng", role=world.binding.role, scope_kind="ORG", scope_id=world.org.pk
    )


def _group_mapping(world):
    GroupRoleMapping.objects.create(
        organization=world.org,
        group_external_id="eng",
        role=world.binding.role,
        scope_kind="TEAM",
        scope_id=world.medops.pk,
    )


def _share(world):
    AppTeamAccess.objects.create(
        registered_app=world.platform_app, team=world.medops, access_level="deployer"
    )


def _groups(world):
    world.member.idp_groups = ["eng", "ops"]
    world.member.save()


def _superuser(world):
    world.user.is_superuser = True
    world.user.save()


def _policy(world):
    Policy.objects.create(
        organization=world.org,
        slug="rev-deny",
        name="Deny prod",
        scope_level="ORG",
        action_pattern="app.deploy",
        resource_pattern={"env": ["production"]},
    )


@pytest.mark.parametrize(
    "change",
    [
        _grant,
        _revoke,
        _hard_delete,
        _expire,
        _edit_role,
        _group_binding,
        _group_mapping,
        _share,
        _groups,
        _superuser,
        _policy,
    ],
)
def test_a_change_to_roles_attributes_or_policies_moves_it(world, change):
    before = revision(world)
    assert before and revision(world) == before
    change(world)
    assert revision(world) != before


def test_a_change_of_session_or_its_attributes_moves_it(world):
    before = revision(world)
    assert revision(world, session_key="s2") != before
    assert revision(world, client_ip="10.0.0.2") != before
    assert revision(world, authenticated_at=timezone.now()) != before


def test_a_bearer_token_change_moves_it(world):
    from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token

    token = ApiToken.objects.create(
        user=world.user, organization=world.org, token_hash="rev", scopes=["read:apps"], name="Rev"
    )
    state = set_current_api_token(token)
    try:
        before = revision(world, session_key=None)
        token.scopes = ["write:apps"]
        token.save()
        assert revision(world, session_key=None) != before
    finally:
        reset_current_api_token(state)


def test_someone_elses_grant_or_another_orgs_policy_leaves_it(world):
    before = revision(world)
    other = make_user("rev-1867-other")
    RoleBinding.objects.create(user=other, role=world.binding.role, scope_kind="ORG", scope_id=world.org.pk)
    elsewhere = ScopeWorld("rev-1867-elsewhere")
    Policy.objects.create(organization=elsewhere.org, slug="rev-other", name="Other", scope_level="ORG")
    assert revision(world) == before


def test_anonymous_has_none():
    with tenant_context(TenantContext(organization_id=None, actor_user_id=None)):
        from astrolift_identity.access_revision import access_revision
        from core.tenancy import get_current_tenant

        assert access_revision(get_current_tenant(), None) == ""
