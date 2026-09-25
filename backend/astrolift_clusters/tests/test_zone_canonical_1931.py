"""One row per DNS zone, whatever the spelling, and teardown never touches a
newcomer's row (#1931)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ManagedDomain
from astrolift_clusters.schema.mutations import ClustersMutation, CreateManagedDomainInput
from astrolift_identity.models import Organization
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, g: None))


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _create(org, zone):
    with tenant_context(TenantContext(organization_id=org.id)):
        return ClustersMutation().create_managed_domain(
            _info(), CreateManagedDomainInput(zone=zone, dns_driver="route53")
        )


@pytest.mark.parametrize("variant", ["globex.example.", "GLOBEX.example", "Globex.Example."])
def test_a_spelling_variant_of_a_registered_zone_is_refused(permission_resolver, variant):
    permission_resolver.grant(Permission.PROVIDER_PLUGIN_CONFIGURE)
    b = Organization.objects.create(name="B", slug=f"b-{uuid.uuid4().hex[:6]}")
    a = Organization.objects.create(name="A", slug=f"a-{uuid.uuid4().hex[:6]}")
    first = _create(b, "globex.example")
    second = _create(a, variant)

    assert first.ok, first.errors
    assert second.ok is False
    assert second.errors[0].code == "CONFLICT"
    assert ManagedDomain.objects.filter(organization=a).count() == 0


def test_a_zone_is_stored_canonical(permission_resolver):
    permission_resolver.grant(Permission.PROVIDER_PLUGIN_CONFIGURE)
    org = Organization.objects.create(name="C", slug=f"c-{uuid.uuid4().hex[:6]}")
    assert _create(org, "Apps.Example.COM.").ok
    assert ManagedDomain.objects.get(organization=org).zone == "apps.example.com"


def test_teardown_picks_the_deleted_row_not_a_newcomer(monkeypatch):
    from astrolift_workflows.activities import deprovision_managed_domain as mod

    old_org = Organization.objects.create(name="Old", slug=f"old-{uuid.uuid4().hex[:6]}")
    new_org = Organization.objects.create(name="New", slug=f"new-{uuid.uuid4().hex[:6]}")
    old = ManagedDomain.objects.create(organization=old_org, zone="shop.example", dns_driver="route53")
    old.provision_cert_id = "old-cert"
    old.save()
    old.soft_delete()
    newcomer = ManagedDomain.objects.create(organization=new_org, zone="shop.example", dns_driver="route53")
    newcomer.provision_cert_id = "new-cert"
    newcomer.save()

    revoked: list[str] = []
    driver = SimpleNamespace(
        revoke_cert=lambda zone, cert: revoked.append(cert),
        deprovision_zone=lambda zone: {"deleted": True, "records_removed": 0},
    )
    monkeypatch.setattr(mod, "_get_cluster_and_dns_driver", lambda cluster_id: (None, driver))

    mod._deprovision_sync(0, "shop.example")

    assert revoked == ["old-cert"]
