"""Managed-domain DNS acts on the hosted zone the platform created, by id (#1931).

The driver found zones by name and the zone id lived in tenant-editable
``dns_config``, so deleting a row registered for a name the install already
hosts deleted that hosted zone, and record writes could land in it.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ManagedDomain
from astrolift_identity.models import Organization

pytestmark = pytest.mark.django_db


def _driver(calls):
    return SimpleNamespace(
        pin_zone=lambda zone, zone_id: calls.append(("pin", zone, zone_id)),
        revoke_cert=lambda zone, cert: calls.append(("revoke", zone, cert)),
        deprovision_zone=lambda zone: calls.append(("delete", zone))
        or {"deleted": True, "records_removed": 3},
        provision_zone=lambda zone: calls.append(("create", zone))
        or {"zone_id": "Z-NEW", "nameservers": ["ns1"]},
    )


def _deleted_row(**fields):
    org = Organization.objects.create(name="o", slug=f"o-{uuid.uuid4().hex[:6]}")
    row = ManagedDomain.objects.create(organization=org, zone="corp.example", dns_driver="route53", **fields)
    row.soft_delete()
    return row


def test_teardown_of_a_registered_zone_never_deletes_a_hosted_zone(monkeypatch):
    from astrolift_workflows.activities import deprovision_managed_domain as mod

    calls: list = []
    _deleted_row(dns_config={"zone_id": "Z-INSTALL"})  # tenant-editable: proves nothing
    monkeypatch.setattr(mod, "_get_cluster_and_dns_driver", lambda cluster_id: (None, _driver(calls)))

    result = mod._deprovision_sync(0, "corp.example")

    assert not any(call[0] == "delete" for call in calls)
    assert result["zone_deleted"] is False


def test_teardown_deletes_only_the_platform_created_zone_by_id(monkeypatch):
    from astrolift_workflows.activities import deprovision_managed_domain as mod

    calls: list = []
    _deleted_row(provision_zone_id="Z-OURS", dns_config={"zone_id": "Z-INSTALL"})
    monkeypatch.setattr(mod, "_get_cluster_and_dns_driver", lambda cluster_id: (None, _driver(calls)))

    mod._deprovision_sync(0, "corp.example")

    assert calls == [("pin", "corp.example", "Z-OURS"), ("delete", "corp.example")]


def test_provisioning_records_the_zone_it_created(monkeypatch):
    from astrolift_workflows.activities import provision_managed_domain as mod

    calls: list = []
    cluster = SimpleNamespace(provider_plugin=SimpleNamespace(slug="aws"))
    monkeypatch.setattr(mod, "_get_cluster_and_dns_driver", lambda cluster_id: (cluster, _driver(calls)))

    mod._provision_dns_zone_sync(7, f"z{uuid.uuid4().hex[:6]}.example")

    row = ManagedDomain.objects.get(provision_zone_id="Z-NEW")
    assert row.provision_zone_id == "Z-NEW"
