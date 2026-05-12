"""Tests for custom Role CRUD (#162)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization, Role
from astrolift_identity.schema.mutations import (
    CreateRoleInput,
    DeleteRoleInput,
    IdentityMutation,
    UpdateRoleInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


def _info():
    user, _ = User.objects.get_or_create(username="admin-rb", defaults={"email": "admin-rb@astrolift.dev"})
    return SimpleNamespace(context=SimpleNamespace(user=user, request=SimpleNamespace(user=user)))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


# ---- create -----------------------------------------------------------


def test_create_role_persists(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with _ctx(org):
        result = IdentityMutation().create_role(
            _info(),
            input=CreateRoleInput(
                slug="dev",
                name="Developer",
                scope_level="ORG",
                permissions=["app.read", "app.deploy"],
            ),
        )

    assert result.ok, result.errors
    role = Role.objects.get(slug="dev")
    assert role.slug == "dev"
    assert role.is_system is False
    assert role.organization_id == org.id
    assert role.permissions == ["app.read", "app.deploy"]


def test_create_role_validates_unknown_permission(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with _ctx(org):
        result = IdentityMutation().create_role(
            _info(),
            input=CreateRoleInput(
                slug="dev",
                name="Developer",
                scope_level="ORG",
                permissions=["app.read", "totally.fake"],
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert "totally.fake" in result.errors[0].message
    assert Role.objects.filter(slug="dev").count() == 0


def test_create_role_rejects_duplicate_slug(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with _ctx(org):
        a = IdentityMutation().create_role(
            _info(),
            input=CreateRoleInput(
                slug="dev",
                name="Developer",
                scope_level="ORG",
                permissions=["app.read"],
            ),
        )
        b = IdentityMutation().create_role(
            _info(),
            input=CreateRoleInput(
                slug="dev",
                name="Developer 2",
                scope_level="ORG",
                permissions=["app.read"],
            ),
        )
    assert a.ok
    assert not b.ok
    assert b.errors[0].code == "CONFLICT"


def test_create_role_rejects_unknown_scope_level(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with _ctx(org):
        result = IdentityMutation().create_role(
            _info(),
            input=CreateRoleInput(
                slug="dev",
                name="Developer",
                scope_level="GALAXY",
                permissions=["app.read"],
            ),
        )
    assert not result.ok
    assert result.errors[0].field == "scopeLevel"


def test_create_role_requires_permission():
    org = Organization.objects.create(name="X", slug="x")
    with _ctx(org):
        result = IdentityMutation().create_role(
            _info(),
            input=CreateRoleInput(
                slug="dev",
                name="Developer",
                scope_level="ORG",
                permissions=["app.read"],
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


# ---- update -----------------------------------------------------------


def test_update_role_swaps_permissions(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with _ctx(org):
        c = IdentityMutation().create_role(
            _info(),
            input=CreateRoleInput(
                slug="dev",
                name="Developer",
                scope_level="ORG",
                permissions=["app.read"],
            ),
        )
        u = IdentityMutation().update_role(
            _info(),
            input=UpdateRoleInput(id=c.data.id, permissions=["app.read", "app.rollback"]),
        )
    assert u.ok
    role = Role.objects.get(slug="dev")
    assert role.permissions == ["app.read", "app.rollback"]


def test_update_rejects_system_role(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    sys_role = Role.objects.create(
        slug="org-admin",
        name="Org admin",
        scope_level=Role.ScopeLevel.ORG,
        permissions=["app.read"],
        is_system=True,
    )

    with _ctx(org):
        result = IdentityMutation().update_role(
            _info(),
            input=UpdateRoleInput(id=str(sys_role.guid), permissions=["app.read", "app.deploy"]),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_update_rejects_unknown_permission(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with _ctx(org):
        c = IdentityMutation().create_role(
            _info(),
            input=CreateRoleInput(
                slug="dev",
                name="Developer",
                scope_level="ORG",
                permissions=["app.read"],
            ),
        )
        u = IdentityMutation().update_role(
            _info(),
            input=UpdateRoleInput(id=c.data.id, permissions=["app.read", "totally.fake"]),
        )
    assert not u.ok
    assert u.errors[0].code == "VALIDATION"


def test_update_cross_org_returns_not_found(permission_resolver):
    org_a = Organization.objects.create(name="A", slug="a")
    org_b = Organization.objects.create(name="B", slug="b")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with _ctx(org_a):
        c = IdentityMutation().create_role(
            _info(),
            input=CreateRoleInput(
                slug="dev",
                name="Developer",
                scope_level="ORG",
                permissions=["app.read"],
            ),
        )

    with _ctx(org_b):
        result = IdentityMutation().update_role(
            _info(),
            input=UpdateRoleInput(id=c.data.id, name="Hacked"),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---- delete -----------------------------------------------------------


def test_delete_role_soft_deletes(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with _ctx(org):
        c = IdentityMutation().create_role(
            _info(),
            input=CreateRoleInput(
                slug="dev",
                name="Developer",
                scope_level="ORG",
                permissions=["app.read"],
            ),
        )
        d = IdentityMutation().soft_delete_role(_info(), input=DeleteRoleInput(id=c.data.id))
    assert d.ok
    role = Role.all_objects.get(slug="dev")
    assert role.deleted_at is not None
    # soft-delete frees the slug for re-use after deletion (per the
    # uniqueness constraint condition).
    assert Role.objects.filter(slug="dev", deleted_at__isnull=True).count() == 0


def test_delete_rejects_system_role(permission_resolver):
    org = Organization.objects.create(name="X", slug="x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    sys_role = Role.objects.create(
        slug="org-admin",
        name="Org admin",
        scope_level=Role.ScopeLevel.ORG,
        permissions=["app.read"],
        is_system=True,
    )

    with _ctx(org):
        result = IdentityMutation().soft_delete_role(_info(), input=DeleteRoleInput(id=str(sys_role.guid)))
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    sys_role.refresh_from_db()
    assert sys_role.deleted_at is None
