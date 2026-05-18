"""Reusable two-tenant fixture for tenant-isolation tests (#537).

Used by tests that need to prove a queryset/resolver filters rows to
the caller's organization. Each fixture call builds two independent
``(organization, user, membership)`` triples so a test can assert that
user-A sees only org-A's rows and never sees org-B's.

To add coverage for a new tenant-scoped model, write a single test in
``core/tests/test_tenant_isolation.py`` that:

  1. Calls ``two_tenants()`` to get the two orgs.
  2. Creates one row in each org's namespace.
  3. Exercises the resolver / queryset / endpoint as user-A.
  4. Asserts user-A sees the A row and never the B row.

The fixture is intentionally tiny so it stays cheap to compose with
the existing GraphQL execution helpers. Heavier setup (Profile
overrides, RBAC group wiring) is the test's responsibility — keep the
fixture itself a thin scaffold.
"""

from __future__ import annotations

import dataclasses
import secrets

import pytest
from django.contrib.auth import get_user_model

User = get_user_model()


@dataclasses.dataclass
class _TenantTriple:
    organization: object
    user: object
    membership: object


@dataclasses.dataclass
class _TwoTenants:
    a: _TenantTriple
    b: _TenantTriple


@pytest.fixture
def two_tenants(db):
    """Build two ``(organization, user, membership)`` triples.

    Each triple has its own organization, its own user with a profile
    whose ``active_organization`` points at the right org, and an
    active OrganizationMember row linking them.
    """
    from organization.models import Organization, OrganizationMember

    def _make(suffix: str) -> _TenantTriple:
        # Random suffix avoids collisions when two tests in the same DB
        # transaction both ask for "org-a" / "org-b".
        nonce = secrets.token_hex(4)
        org = Organization.objects.create(name=f"Org-{suffix}-{nonce}")
        user = User.objects.create_user(
            username=f"user-{suffix}-{nonce}",
            email=f"user-{suffix}-{nonce}@example.test",
            password="x",
        )
        membership = OrganizationMember.objects.create(
            organization=org,
            member=user,
            is_active=True,
        )
        profile = user.profile
        profile.active_organization = org
        profile.save()
        return _TenantTriple(organization=org, user=user, membership=membership)

    return _TwoTenants(a=_make("a"), b=_make("b"))


__all__ = ["two_tenants"]
