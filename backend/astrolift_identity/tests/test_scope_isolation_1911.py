"""A selected team or project from another org grants nothing (#1911)."""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization, Team
from astrolift_identity.permission_resolver import _candidate_scopes
from core.tenancy import TenantContext

pytestmark = pytest.mark.django_db


def test_a_foreign_team_is_not_a_candidate_scope():
    a = Organization.objects.create(name="A", slug="a-1911")
    b = Organization.objects.create(name="B", slug="b-1911")
    own = Team.objects.create(organization=a, name="own", slug="own-1911")
    foreign = Team.objects.create(organization=b, name="foreign", slug="foreign-1911")

    assert ("TEAM", own.pk) in _candidate_scopes(TenantContext(organization_id=a.pk, team_id=own.pk), None)
    assert ("TEAM", foreign.pk) not in _candidate_scopes(
        TenantContext(organization_id=a.pk, team_id=foreign.pk), None
    )
