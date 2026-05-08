"""
``astrolift_my_permissions`` — read-side mirror of @require_permission.

The UI calls this once on layout mount to drive nav filtering and
action gating. Anonymous callers must get an empty list (no slug
catalog leak); authenticated viewers get a sorted, deduped union of
every RoleBinding's role.permissions in scope.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_identity.models import Organization, Role, RoleBinding
from astrolift_identity.schema.queries import IdentityQuery
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


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-perms-test")


@pytest.fixture
def user():
    User = get_user_model()
    return User.objects.create(username="viewer@test", email="viewer@test")


@pytest.fixture
def fake_info():
    return SimpleNamespace(context=SimpleNamespace(request=None))


def _bind(user, org, role_slug, perms):
    role = Role.objects.create(
        name=role_slug,
        slug=role_slug,
        scope_level=Role.ScopeLevel.ORG,
        permissions=perms,
        is_system=False,
    )
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.id)


def test_anonymous_rejected_by_tenant_scope(fake_info):
    """Anonymous (no tenant context) is rejected upstream by
    @tenant_scoped before the resolver body runs — that's the
    contract, no slug catalog leak past the tenant gate."""
    from core.decorators import TenantRequired

    q = IdentityQuery()
    with tenant_context(TenantContext(organization_id=None, actor_user_id=None)):
        with pytest.raises(TenantRequired):
            q.astrolift_my_permissions(fake_info)


def test_authed_user_in_tenant_with_no_user(org, fake_info):
    """Tenant exists but actor_user_id is None — defensive guard
    inside the resolver returns an empty list rather than walking
    the bindings table with user_id=None."""
    q = IdentityQuery()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=None)):
        result = q.astrolift_my_permissions(fake_info)
    assert result == []


def test_authed_user_with_no_bindings(org, user, fake_info):
    q = IdentityQuery()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = q.astrolift_my_permissions(fake_info)
    assert result == []


def test_unions_permissions_across_roles(org, user, fake_info):
    _bind(user, org, "viewer", ["app.read", "team.read"])
    _bind(user, org, "deployer", ["app.read", "app.deploy"])

    q = IdentityQuery()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = q.astrolift_my_permissions(fake_info)

    # Sorted, deduped union of both bindings.
    assert result == ["app.deploy", "app.read", "team.read"]


def test_expired_binding_excluded(org, user, fake_info):
    role = Role.objects.create(
        name="ex-role",
        slug="ex-role",
        scope_level=Role.ScopeLevel.ORG,
        permissions=["app.delete"],
        is_system=False,
    )
    RoleBinding.objects.create(
        user=user,
        role=role,
        scope_kind="ORG",
        scope_id=org.id,
        expires_at=timezone.now() - timedelta(days=1),
    )
    q = IdentityQuery()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = q.astrolift_my_permissions(fake_info)
    assert "app.delete" not in result


def test_other_org_binding_not_visible(org, user, fake_info):
    other_org = Organization.objects.create(name="Other", slug="other-org-perms")
    _bind(user, other_org, "other-admin", ["org.delete"])

    q = IdentityQuery()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = q.astrolift_my_permissions(fake_info)
    # Tenant-scoped to org, so the other-org binding doesn't bleed in.
    assert result == []


def test_superuser_bypass_grants_every_permission(org, fake_info):
    """Django superusers see the full slug catalog without explicit
    bindings — matches the resolver's bypass and keeps the bootstrap
    admin path simple (one Django superuser, no role plumbing)."""
    from core.permissions import Permission

    User = get_user_model()
    su = User.objects.create_user(username="root@local", email="root@local", is_superuser=True, is_staff=True)

    q = IdentityQuery()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=su.id)):
        result = q.astrolift_my_permissions(fake_info)

    expected = sorted(p.value for p in Permission)
    assert result == expected
    # Sanity check on a few critical slugs.
    for slug in ("app.deploy", "org.delete", "cluster.unregister"):
        assert slug in result


def test_inactive_superuser_does_not_bypass(org, fake_info):
    """is_active=False superusers are dormant accounts; the bypass
    must not grant anything to them. Real RoleBindings still apply
    (none here, so the result is empty)."""
    User = get_user_model()
    su = User.objects.create_user(
        username="dormant@local",
        email="dormant@local",
        is_superuser=True,
        is_staff=True,
        is_active=False,
    )

    q = IdentityQuery()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=su.id)):
        result = q.astrolift_my_permissions(fake_info)

    assert result == []
