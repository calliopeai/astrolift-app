"""
Smoke tests for the Role / RoleBinding / permission-resolver chain.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization, Role, RoleBinding
from astrolift_identity.permission_resolver import resolve
from core.permissions import Permission
from core.tenancy import TenantContext

pytestmark = pytest.mark.django_db


def _user(email="alice@example.com"):
    User = get_user_model()
    return User.objects.create(username=email, email=email)


def test_role_rejects_unknown_permission():
    Role.objects.create(
        name="bogus",
        slug="bogus",
        scope_level=Role.ScopeLevel.ORG,
        permissions=["org.read", "not.a.real.permission"],
        is_system=False,
    )
    bogus = Role.objects.get(slug="bogus")
    with pytest.raises(Exception):  # ValidationError
        bogus.full_clean()


def test_resolver_grants_via_org_binding():
    org = Organization.objects.create(name="Acme", slug="acme")
    user = _user()
    role = Role.objects.create(
        name="org-admin",
        slug="org-admin-test",
        scope_level=Role.ScopeLevel.ORG,
        permissions=[Permission.APP_DEPLOY.value],
        is_system=True,
    )
    RoleBinding.objects.create(
        user=user, role=role, scope_kind="ORG", scope_id=org.id
    )

    tenant = TenantContext(organization_id=org.id, actor_user_id=user.id)
    granted, _ = resolve(tenant, Permission.APP_DEPLOY, scope=None)
    assert granted is True


def test_resolver_denies_when_no_binding():
    org = Organization.objects.create(name="Acme", slug="acme")
    user = _user()
    tenant = TenantContext(organization_id=org.id, actor_user_id=user.id)
    granted, _ = resolve(tenant, Permission.APP_DEPLOY, scope=None)
    assert granted is False
