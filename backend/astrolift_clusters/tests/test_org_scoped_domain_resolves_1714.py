"""A zone registered for an org actually gets used (#1714).

``createManagedDomain`` defaults to ``organizationScoped=true``, and
nothing in the codebase writes ``organization.default_managed_domain`` --
no mutation, no management command. The resolver's second step only
matched platform-level rows, so the domain an operator registered through
the supported path was accepted, listed, and matched by nothing, and
their apps still came up with no hostname.

Same shape as #1689 (``default_for`` defaulting to ``none``), one level
down: a registration that looks complete and is inert.
"""

from __future__ import annotations

import pytest

from astrolift_clusters.models import ManagedDomain, resolve_managed_domain
from astrolift_identity.models import Organization

pytestmark = pytest.mark.django_db


@pytest.fixture
def org(db):
    return Organization.objects.create(name="Conflict", slug="conflict-1714")


@pytest.fixture
def other_org(db):
    return Organization.objects.create(name="Acme", slug="acme-1714")


def _domain(*, zone, organization=None, default_for="tenant_apps"):
    return ManagedDomain.objects.create(
        organization=organization,
        zone=zone,
        dns_driver="route53",
        default_for=default_for,
    )


def test_an_org_scoped_domain_resolves_for_that_org(org):
    domain = _domain(zone="conflict.example", organization=org)

    assert resolve_managed_domain(org) == domain


def test_an_org_scoped_domain_does_not_resolve_for_another_org(org, other_org):
    _domain(zone="conflict.example", organization=org)

    assert resolve_managed_domain(other_org) is None


def test_the_orgs_own_domain_outranks_the_platform_default(org):
    _domain(zone="platform.example", organization=None)
    own = _domain(zone="conflict.example", organization=org)

    assert resolve_managed_domain(org) == own


def test_an_explicit_org_default_still_wins(org):
    pinned = _domain(zone="pinned.example", organization=org, default_for="none")
    _domain(zone="conflict.example", organization=org)
    org.default_managed_domain = pinned
    org.save(update_fields=["default_managed_domain"])

    assert resolve_managed_domain(org) == pinned


def test_default_for_none_is_still_ignored(org):
    """Registering a zone and meaning "do not use it" keeps working."""
    _domain(zone="conflict.example", organization=org, default_for="none")

    assert resolve_managed_domain(org) is None


def test_preview_and_tenant_use_cases_stay_separate(org):
    _domain(zone="apps.example", organization=org, default_for="tenant_apps")
    previews = _domain(zone="preview.example", organization=org, default_for="preview_envs")

    assert resolve_managed_domain(org, for_preview=True) == previews


def test_both_covers_either_use_case(org):
    domain = _domain(zone="conflict.example", organization=org, default_for="both")

    assert resolve_managed_domain(org) == domain
    assert resolve_managed_domain(org, for_preview=True) == domain


def test_a_soft_deleted_org_domain_falls_through_to_the_platform(org):
    from django.utils import timezone

    platform = _domain(zone="platform.example", organization=None)
    own = _domain(zone="conflict.example", organization=org)
    own.deleted_at = timezone.now()
    own.save(update_fields=["deleted_at"])

    assert resolve_managed_domain(org) == platform


def test_no_organization_still_resolves_the_platform_default():
    platform = _domain(zone="platform.example", organization=None)

    assert resolve_managed_domain(None) == platform
