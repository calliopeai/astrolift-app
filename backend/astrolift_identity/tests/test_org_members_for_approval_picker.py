"""Tests for ``astroliftOrgMembersForApprovalPicker`` (#410).

Powers the approval-policy picker on the register-app wizard and on
the per-app Settings page. The query is org-scoped, deny-by-default
without ``org.manage_members``, and refuses cross-tenant slugs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Member, Organization
from astrolift_identity.schema.queries import IdentityQuery
from auth1.models import UserInfo
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def actor():
    User = get_user_model()
    return User.objects.create(username="caller", email="caller@example")


@pytest.fixture
def info(actor):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=actor)))


def _add_org_member(org, *, username, email, first_name="", last_name=""):
    User = get_user_model()
    user = User.objects.create(username=username, email=email, first_name=first_name, last_name=last_name)
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    return user


def test_returns_active_org_members(actor, info, permission_resolver):
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org = Organization.objects.create(name="Acme", slug="acme-picker")
    alice = _add_org_member(
        org, username="alice", email="alice@example.com", first_name="Alice", last_name="Smith"
    )
    bob = _add_org_member(org, username="bob", email="bob@example.com")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        results = IdentityQuery().astrolift_org_members_for_approval_picker(info, org_slug="acme-picker")

    by_id = {r.id: r for r in results}
    assert str(alice.pk) in by_id
    assert str(bob.pk) in by_id
    assert by_id[str(alice.pk)].display_name == "Alice Smith"
    # Fall back to username when no first/last name.
    assert by_id[str(bob.pk)].display_name == "bob"
    assert by_id[str(alice.pk)].email == "alice@example.com"
    # No Auth0 UserInfo -> empty avatar.
    assert by_id[str(alice.pk)].avatar_url == ""


def test_excludes_inactive_and_soft_deleted_members(actor, info, permission_resolver):
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org = Organization.objects.create(name="Acme", slug="acme-picker-2")
    active = _add_org_member(org, username="active", email="active@example.com")
    inactive = _add_org_member(org, username="inactive", email="inactive@example.com")
    Member.objects.filter(user=inactive).update(is_active=False)
    soft_deleted = _add_org_member(org, username="gone", email="gone@example.com")
    Member.objects.filter(user=soft_deleted).update(deleted_at="2025-01-01T00:00:00Z")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        results = IdentityQuery().astrolift_org_members_for_approval_picker(info, org_slug="acme-picker-2")

    ids = {r.id for r in results}
    assert str(active.pk) in ids
    assert str(inactive.pk) not in ids
    assert str(soft_deleted.pk) not in ids


def test_refuses_cross_tenant_slug(actor, info, permission_resolver):
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    home = Organization.objects.create(name="Home", slug="home-org-picker")
    other = Organization.objects.create(name="Other", slug="other-org-picker")
    _add_org_member(other, username="outsider", email="outsider@other.com")

    with tenant_context(TenantContext(organization_id=home.id, actor_user_id=actor.id)):
        results = IdentityQuery().astrolift_org_members_for_approval_picker(info, org_slug="other-org-picker")

    # Cross-tenant -> empty list (fail closed, don't leak existence).
    assert results == []


def test_uses_userinfo_for_avatar_and_display_fallback(actor, info, permission_resolver):
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org = Organization.objects.create(name="Acme", slug="acme-picker-avatar")
    user = _add_org_member(org, username="useronly", email="userify@example.com")
    UserInfo.objects.create(
        internal_user=user,
        given_name="",
        family_name="",
        nickname="Use Rify",
        name="",
        picture="https://cdn.example/avatar.png",
        locale="en",
        updated_at="2025-01-01T00:00:00Z",
        email="userify@example.com",
        email_verified=True,
        iss="https://idp.example",
        aud="client",
        iat=0,
        exp=0,
        sub="userify-sub",
        sid="sid",
        nonce="nonce",
    )

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        results = IdentityQuery().astrolift_org_members_for_approval_picker(
            info, org_slug="acme-picker-avatar"
        )

    [row] = results
    assert row.id == str(user.pk)
    # No first/last name; nickname from UserInfo is used as display label.
    assert row.display_name == "Use Rify"
    assert row.avatar_url == "https://cdn.example/avatar.png"


def test_denied_without_permission(actor, info, permission_resolver):
    from core.permissions import PermissionDenied

    org = Organization.objects.create(name="Acme", slug="acme-picker-deny")
    _add_org_member(org, username="someone", email="someone@example.com")

    # No permission grant — the @require_permission decorator should
    # refuse the call before tenant_scoped runs.
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        with pytest.raises(PermissionDenied):
            IdentityQuery().astrolift_org_members_for_approval_picker(info, org_slug="acme-picker-deny")


def test_refuses_without_tenant(info, permission_resolver):
    from core.decorators import TenantRequired

    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    # No tenant context -> @tenant_scoped refuses with TenantRequired.
    with pytest.raises(TenantRequired):
        IdentityQuery().astrolift_org_members_for_approval_picker(info, org_slug="anything")
