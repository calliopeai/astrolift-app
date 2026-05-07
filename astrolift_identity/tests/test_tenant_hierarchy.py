"""
Smoke tests for the tenant-hierarchy models.

Cover:

* Slug uniqueness is partial — a soft-deleted slug can be re-claimed.
* ``Project.save`` denormalizes ``organization_id`` from its team.
* The default manager hides soft-deleted rows; ``all_objects`` exposes
  them.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError

from astrolift_identity.models import Organization, Project, Team


pytestmark = pytest.mark.django_db


def _make_org(slug: str = "acme") -> Organization:
    return Organization.objects.create(name=slug.title(), slug=slug)


def test_org_slug_unique_among_active():
    _make_org("acme")
    with pytest.raises(IntegrityError):
        _make_org("acme")


def test_deleted_org_slug_can_be_reclaimed():
    org = _make_org("acme")
    org.soft_delete()
    again = _make_org("acme")
    assert again.pk != org.pk
    assert Organization.objects.filter(slug="acme").count() == 1


def test_team_slug_unique_per_org():
    a = _make_org("a")
    b = _make_org("b")
    Team.objects.create(name="eng", slug="eng", organization=a)
    Team.objects.create(name="eng", slug="eng", organization=b)  # ok across orgs
    with pytest.raises(IntegrityError):
        Team.objects.create(name="eng", slug="eng", organization=a)


def test_project_denormalizes_organization_from_team():
    org = _make_org("acme")
    team = Team.objects.create(name="eng", slug="eng", organization=org)
    project = Project.objects.create(name="api", slug="api", team=team)
    assert project.organization_id == org.id


def test_default_manager_hides_deleted():
    org = _make_org("acme")
    org.soft_delete()
    assert Organization.objects.filter(pk=org.pk).exists() is False
    assert Organization.all_objects.filter(pk=org.pk).exists() is True
