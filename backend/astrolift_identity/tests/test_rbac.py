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


def test_resolver_grants_to_django_superuser_without_binding():
    """Bootstrap-admin path: a fresh install has a Django superuser
    but no RoleBindings yet. They should still pass every permission
    check so they can complete first-run onboarding."""
    User = get_user_model()
    su = User.objects.create(
        username="root@example", email="root@example",
        is_superuser=True, is_staff=True,
    )
    org = Organization.objects.create(name="Acme", slug="acme-su")
    tenant = TenantContext(organization_id=org.id, actor_user_id=su.id)

    granted, reason = resolve(tenant, Permission.APP_DEPLOY, scope=None)
    assert granted is True
    assert "superuser" in reason

    granted2, _ = resolve(tenant, Permission.ORG_DELETE, scope=None)
    assert granted2 is True


def test_resolver_does_not_grant_to_inactive_superuser():
    User = get_user_model()
    su = User.objects.create(
        username="dormant", email="dormant@example",
        is_superuser=True, is_active=False,
    )
    org = Organization.objects.create(name="Acme", slug="acme-dormant")
    tenant = TenantContext(organization_id=org.id, actor_user_id=su.id)

    granted, _ = resolve(tenant, Permission.APP_DEPLOY, scope=None)
    assert granted is False
