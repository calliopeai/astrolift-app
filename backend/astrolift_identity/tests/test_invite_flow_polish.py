"""Tests for the member invite-flow polish bundle (#418).

Covers:

* ``astroliftSearchableUsers`` — de-dupe search the InviteDialog runs
  before issuing a duplicate invitation. Permission gate, happy path
  (member + invitation matches), empty-query/empty-result, cross-tenant
  isolation, the org-scope visibility boundary.
* ``astroliftRolesIcanGrant`` — the role picker filter. Permission
  gate, viewer-with-no-effective-perms returns []; viewer with a
  subset returns matching roles; Django superuser returns every role.
* ``AstroliftInvitation`` invitedBy enrichment — the list resolver
  populates ``invited_by_display_name`` / ``invited_by_email`` /
  ``invited_by_avatar_url`` / ``invited_by_user_id`` from the inviter
  user and (when present) their Auth0 ``UserInfo`` row.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import (
    Invitation,
    Member,
    Organization,
    Role,
    RoleBinding,
)
from astrolift_identity.schema.mutations import CreateInvitationInput, IdentityMutation
from astrolift_identity.schema.queries import IdentityQuery
from auth1.models import UserInfo
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def actor():
    return User.objects.create(username="caller", email="caller@example.com")


@pytest.fixture
def info(actor):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=actor)))


def _add_org_member(org, *, username, email, first_name="", last_name=""):
    user = User.objects.create(username=username, email=email, first_name=first_name, last_name=last_name)
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    return user


# ---- astroliftSearchableUsers ----------------------------------------


def test_searchable_users_returns_member_matches(actor, info, permission_resolver):
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org = Organization.objects.create(name="Acme", slug="su-acme-1")
    alice = _add_org_member(
        org, username="alice", email="alice@acme.test", first_name="Alice", last_name="Smith"
    )
    _add_org_member(org, username="bob", email="bob@acme.test")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        rows = IdentityQuery().astrolift_searchable_users(info, query="alice")

    assert len(rows) == 1
    [r] = rows
    assert r.match_kind == "MEMBER"
    assert r.user_id == str(alice.pk)
    assert r.email == "alice@acme.test"
    assert r.display_label == "Alice Smith"
    assert r.invitation_id is None


def test_searchable_users_returns_invitation_matches(actor, info, permission_resolver):
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org = Organization.objects.create(name="Acme", slug="su-acme-2")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        IdentityMutation().create_invitation(info, input=CreateInvitationInput(email="pending@acme.test"))
        rows = IdentityQuery().astrolift_searchable_users(info, query="pending")

    assert len(rows) == 1
    [r] = rows
    assert r.match_kind == "INVITATION"
    assert r.email == "pending@acme.test"
    assert r.invitation_id is not None
    assert r.invitation_status == Invitation.Status.PENDING
    assert r.expires_at is not None
    assert r.user_id is None


def test_searchable_users_combines_member_and_invitation_rows(actor, info, permission_resolver):
    """A single query can match both an existing member and a pending
    invitation — the FE renders them together so the operator sees
    'this person is already in' AND 'and you already invited them'."""
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org = Organization.objects.create(name="Acme", slug="su-acme-3")
    _add_org_member(org, username="zoe", email="zoe@acme.test")
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        IdentityMutation().create_invitation(info, input=CreateInvitationInput(email="zoe-friend@acme.test"))
        rows = IdentityQuery().astrolift_searchable_users(info, query="zoe")

    kinds = sorted(r.match_kind for r in rows)
    assert kinds == ["INVITATION", "MEMBER"]


def test_searchable_users_empty_query_returns_empty(actor, info, permission_resolver):
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org = Organization.objects.create(name="Acme", slug="su-acme-4")
    _add_org_member(org, username="who", email="who@acme.test")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        assert IdentityQuery().astrolift_searchable_users(info, query="") == []
        assert IdentityQuery().astrolift_searchable_users(info, query="   ") == []


def test_searchable_users_no_match_returns_empty(actor, info, permission_resolver):
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org = Organization.objects.create(name="Acme", slug="su-acme-5")
    _add_org_member(org, username="who", email="who@acme.test")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        rows = IdentityQuery().astrolift_searchable_users(info, query="nonexistent-xyz")

    assert rows == []


def test_searchable_users_excludes_cross_tenant_members(actor, info, permission_resolver):
    """A member of org-B must not be findable when the active tenant
    is org-A — even if the query string happens to match. This is the
    PII isolation guarantee for cross-org probes."""
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org_a = Organization.objects.create(name="A", slug="su-org-a")
    org_b = Organization.objects.create(name="B", slug="su-org-b")
    _add_org_member(org_b, username="bob-b", email="bob@org-b.test", first_name="Bob", last_name="OrgB")

    with tenant_context(TenantContext(organization_id=org_a.id, actor_user_id=actor.id)):
        rows = IdentityQuery().astrolift_searchable_users(info, query="bob")

    assert rows == []


def test_searchable_users_excludes_inactive_and_soft_deleted_members(actor, info, permission_resolver):
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org = Organization.objects.create(name="Acme", slug="su-acme-6")
    active = _add_org_member(org, username="active-x", email="ax@acme.test")
    inactive = _add_org_member(org, username="inactive-x", email="ix@acme.test")
    Member.objects.filter(user=inactive).update(is_active=False)
    soft = _add_org_member(org, username="soft-x", email="sx@acme.test")
    Member.objects.filter(user=soft).update(deleted_at="2025-01-01T00:00:00Z")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        rows = IdentityQuery().astrolift_searchable_users(info, query="x")

    user_ids = {r.user_id for r in rows if r.user_id}
    assert str(active.pk) in user_ids
    assert str(inactive.pk) not in user_ids
    assert str(soft.pk) not in user_ids


def test_searchable_users_requires_permission(actor, info):
    from core.permissions import PermissionDenied

    org = Organization.objects.create(name="Acme", slug="su-acme-deny")
    _add_org_member(org, username="who", email="who@acme.test")
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        with pytest.raises(PermissionDenied):
            IdentityQuery().astrolift_searchable_users(info, query="who")


def test_searchable_users_populates_avatar_from_userinfo(actor, info, permission_resolver):
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org = Organization.objects.create(name="Acme", slug="su-acme-avatar")
    u = _add_org_member(org, username="hasinfo", email="hasinfo@acme.test")
    UserInfo.objects.create(
        internal_user=u,
        given_name="",
        family_name="",
        nickname="Has Info",
        name="",
        picture="https://cdn.example/avatar-x.png",
        locale="en",
        updated_at="2025-01-01T00:00:00Z",
        email="hasinfo@acme.test",
        email_verified=True,
        iss="https://idp.example",
        aud="client",
        iat=0,
        exp=0,
        sub="hasinfo-sub",
        sid="sid",
        nonce="nonce",
    )

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        [row] = IdentityQuery().astrolift_searchable_users(info, query="hasinfo")

    assert row.avatar_url == "https://cdn.example/avatar-x.png"
    assert row.display_label == "Has Info"


# ---- astroliftRolesIcanGrant ----------------------------------------


def test_roles_i_can_grant_returns_subset_of_viewer_perms(actor, info, permission_resolver):
    """The viewer holds {app.read}. A role with permissions {app.read}
    is grantable; a role with {app.read, app.deploy} is not."""
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org = Organization.objects.create(name="Acme", slug="rcg-acme-1")
    ok_role = Role.objects.create(
        organization=org,
        slug="just-read",
        name="Just Read",
        scope_level=Role.ScopeLevel.ORG,
        permissions=[Permission.APP_READ.value],
    )
    too_big_role = Role.objects.create(
        organization=org,
        slug="read-and-deploy",
        name="Read+Deploy",
        scope_level=Role.ScopeLevel.ORG,
        permissions=[Permission.APP_READ.value, Permission.APP_DEPLOY.value],
    )

    # Give the viewer just APP_READ via a binding.
    viewer_role = Role.objects.create(
        organization=org,
        slug="viewer-grant-source",
        name="Viewer Source",
        scope_level=Role.ScopeLevel.ORG,
        permissions=[Permission.APP_READ.value],
    )
    RoleBinding.objects.create(
        user=actor,
        role=viewer_role,
        scope_kind=RoleBinding.ScopeKind.ORG,
        scope_id=org.id,
    )

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        rows = IdentityQuery().astrolift_roles_i_can_grant(info)

    slugs = {r.slug for r in rows}
    assert ok_role.slug in slugs
    assert viewer_role.slug in slugs
    assert too_big_role.slug not in slugs


def test_roles_i_can_grant_returns_empty_when_viewer_has_no_perms(actor, info, permission_resolver):
    """The viewer holds ORG_MANAGE_MEMBERS *only* (granted via the
    decorator harness, not a real RoleBinding). With no RoleBindings
    they have no effective permissions — the picker collapses to
    empty so the FE can disable with an explainer."""
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org = Organization.objects.create(name="Acme", slug="rcg-acme-2")
    Role.objects.create(
        organization=org,
        slug="needs-deploy",
        name="Needs Deploy",
        scope_level=Role.ScopeLevel.ORG,
        permissions=[Permission.APP_DEPLOY.value],
    )

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        rows = IdentityQuery().astrolift_roles_i_can_grant(info)

    assert rows == []


def test_roles_i_can_grant_superuser_sees_every_role(info, permission_resolver):
    """Django superuser bypasses the subset check — mirrors the
    bootstrap admin convention used elsewhere in the resolver chain."""
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    su = User.objects.create(username="super", email="super@example.com", is_superuser=True)
    su_info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=su)))
    org = Organization.objects.create(name="Acme", slug="rcg-acme-3")
    a = Role.objects.create(
        organization=org,
        slug="role-a",
        name="A",
        scope_level=Role.ScopeLevel.ORG,
        permissions=[Permission.APP_DEPLOY.value],
    )
    b = Role.objects.create(
        organization=org,
        slug="role-b",
        name="B",
        scope_level=Role.ScopeLevel.ORG,
        permissions=[Permission.ORG_DELETE.value],
    )

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=su.id)):
        rows = IdentityQuery().astrolift_roles_i_can_grant(su_info)

    slugs = {r.slug for r in rows}
    assert a.slug in slugs
    assert b.slug in slugs


def test_roles_i_can_grant_requires_permission(actor, info):
    from core.permissions import PermissionDenied

    org = Organization.objects.create(name="Acme", slug="rcg-acme-deny")
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        with pytest.raises(PermissionDenied):
            IdentityQuery().astrolift_roles_i_can_grant(info)


def test_roles_i_can_grant_excludes_other_org_custom_roles(actor, info, permission_resolver):
    """A custom role on org-B must not appear when the active tenant
    is org-A. System roles (no org binding) still appear in both."""
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org_a = Organization.objects.create(name="A", slug="rcg-org-a")
    org_b = Organization.objects.create(name="B", slug="rcg-org-b")
    # Give the viewer org_admin-equivalent on org_a so the subset
    # check doesn't filter every role out for an unrelated reason.
    super_role = Role.objects.create(
        organization=org_a,
        slug="superduper-a",
        name="Super A",
        scope_level=Role.ScopeLevel.ORG,
        permissions=[p.value for p in Permission],
    )
    RoleBinding.objects.create(
        user=actor,
        role=super_role,
        scope_kind=RoleBinding.ScopeKind.ORG,
        scope_id=org_a.id,
    )
    b_role = Role.objects.create(
        organization=org_b,
        slug="custom-on-b",
        name="On B",
        scope_level=Role.ScopeLevel.ORG,
        permissions=[Permission.APP_READ.value],
    )

    with tenant_context(TenantContext(organization_id=org_a.id, actor_user_id=actor.id)):
        rows = IdentityQuery().astrolift_roles_i_can_grant(info)

    slugs = {r.slug for r in rows}
    assert super_role.slug in slugs
    assert b_role.slug not in slugs


# ---- InvitedBy enrichment on AstroliftInvitation --------------------


def test_invitation_list_populates_inviter_attribution(actor, info, permission_resolver):
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org = Organization.objects.create(name="Acme", slug="inv-by-acme-1")
    inviter = User.objects.create(
        username="thalia",
        email="thalia@acme.test",
        first_name="Thalia",
        last_name="Inviter",
    )
    inviter_info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=inviter)))

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=inviter.id)):
        IdentityMutation().create_invitation(inviter_info, input=CreateInvitationInput(email="new@acme.test"))

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        [inv] = IdentityQuery().astrolift_invitations(info)

    assert inv.invited_by_username == "thalia"
    assert inv.invited_by_user_id == str(inviter.pk)
    assert inv.invited_by_email == "thalia@acme.test"
    assert inv.invited_by_display_name == "Thalia Inviter"
    assert inv.invited_by_avatar_url == ""  # No UserInfo row


def test_invitation_list_pulls_avatar_from_userinfo(actor, info, permission_resolver):
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org = Organization.objects.create(name="Acme", slug="inv-by-acme-2")
    inviter = User.objects.create(username="picasso", email="p@acme.test")
    UserInfo.objects.create(
        internal_user=inviter,
        given_name="",
        family_name="",
        nickname="",
        name="",
        picture="https://cdn.example/picasso.png",
        locale="en",
        updated_at="2025-01-01T00:00:00Z",
        email="p@acme.test",
        email_verified=True,
        iss="https://idp.example",
        aud="client",
        iat=0,
        exp=0,
        sub="picasso-sub",
        sid="sid",
        nonce="nonce",
    )
    inviter_info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=inviter)))

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=inviter.id)):
        IdentityMutation().create_invitation(
            inviter_info, input=CreateInvitationInput(email="next@acme.test")
        )

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        [inv] = IdentityQuery().astrolift_invitations(info)

    assert inv.invited_by_avatar_url == "https://cdn.example/picasso.png"
    assert inv.invited_by_user_id == str(inviter.pk)
