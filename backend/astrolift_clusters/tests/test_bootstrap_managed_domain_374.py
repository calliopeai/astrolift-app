"""The installer hands a fresh install its apps zone (calliope-installer#374).

Without it a new install deployed every app with no platform hostname until an
operator registered the zone by hand. Create-only: an existing row, including
one an operator has since edited, is never touched.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from astrolift_clusters.models import ManagedDomain
from astrolift_clusters.models.managed_domain import resolve_managed_domain

pytestmark = pytest.mark.django_db

ENV = {
    "ASTROLIFT_MANAGED_DOMAIN_ZONE": "Apps.Example.net.",
    "ASTROLIFT_MANAGED_DOMAIN_ZONE_ID": "Z0123456789ABC",
    "ASTROLIFT_MANAGED_DOMAIN_CERTIFICATE_ARN": "arn:aws:acm:us-west-2:123456789012:certificate/abc",
}


def _run(monkeypatch, env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    call_command("bootstrap_managed_domain")


def test_it_registers_the_zone_as_the_platform_default_for_apps(monkeypatch):
    _run(monkeypatch, ENV)

    domain = ManagedDomain.objects.get(zone="apps.example.net")
    assert domain.organization is None
    assert domain.dns_config == {
        "zone_id": "Z0123456789ABC",
        "certificate_arn": "arn:aws:acm:us-west-2:123456789012:certificate/abc",
    }
    assert domain.is_wildcard_managed is True
    assert domain.provision_state == "mark_active"
    # What a new app environment resolves to, with no org default set.
    assert resolve_managed_domain(None) == domain


def test_an_existing_row_is_left_as_it_is(monkeypatch):
    _run(monkeypatch, ENV)
    domain = ManagedDomain.objects.get(zone="apps.example.net")
    domain.default_for = ManagedDomain.DefaultFor.BOTH
    domain.save(update_fields=["default_for"])

    _run(monkeypatch, {**ENV, "ASTROLIFT_MANAGED_DOMAIN_ZONE_ID": "ZOTHER"})

    domain.refresh_from_db()
    assert domain.default_for == ManagedDomain.DefaultFor.BOTH
    assert domain.dns_config["zone_id"] == "Z0123456789ABC"
    assert ManagedDomain.objects.filter(zone="apps.example.net").count() == 1


def test_no_zone_is_a_no_op(monkeypatch):
    monkeypatch.delenv("ASTROLIFT_MANAGED_DOMAIN_ZONE", raising=False)

    call_command("bootstrap_managed_domain")

    assert ManagedDomain.objects.count() == 0
