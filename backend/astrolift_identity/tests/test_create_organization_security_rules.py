"""``create_organization`` seeds the org-wide security burst rules (#151).

A new tenant has to arrive with brute-force detection already on: the
detectors are org-scoped, and there is no ``org.created`` platform Event
to hang the seeding off, so the mutation is the hook.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization
from astrolift_identity.schema.mutations import (
    CreateOrganizationInput,
    IdentityMutation,
)
from astrolift_operations.models import AlertRule
from core.permissions import Permission

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _create(slug: str):
    return IdentityMutation().create_organization(
        _info(),
        input=CreateOrganizationInput(name="Acme", slug=slug),
    )


def test_create_organization_seeds_the_security_rules(permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)

    result = _create("acme-sec-rules")

    assert result.ok is True
    org = Organization.objects.get(slug="acme-sec-rules")
    kinds = {r.predicate["kind"] for r in AlertRule.objects.filter(organization=org)}
    assert kinds == {"failed_login_burst", "permission_denied_burst"}


def test_seeding_failure_does_not_fail_the_org_create(permission_resolver, monkeypatch):
    """Alert config is best-effort: the org still exists if seeding blows up."""
    permission_resolver.grant(Permission.ORG_UPDATE)
    monkeypatch.setattr(
        "astrolift_operations.alert_seed.seed_security_alert_rules",
        lambda org: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = _create("acme-sec-rules-boom")

    assert result.ok is True
    assert Organization.objects.filter(slug="acme-sec-rules-boom").exists()
