"""The driver-keyed DNS zone and certificate pickers are the operator's (#1932).

They list through the platform's ambient credentials, and that account holds
every tenant's hosted zones and certificates, so an org admin saw other orgs'
zone names, zone ids and certificate ARNs. A tenant gets ``supported=False``
(the dialog's manual-entry path) and the account is never listed.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_clusters.schema.queries import ClustersQuery
from astrolift_identity.models import Organization
from core import dns_discovery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def listed():
    calls = []
    fake = SimpleNamespace(
        list_zones=lambda: calls.append("zones")
        or [{"id": "ZB", "name": "globex.example.", "private": False, "config_json": "{}"}],
        list_certificates=lambda: calls.append("certs")
        or [
            {
                "arn": "arn:aws:acm:x:1:certificate/b",
                "name": "b",
                "domain_name": "*.globex.example",
                "status": "ISSUED",
            }
        ],
    )
    dns_discovery.set_dns_driver_for_tests(fake)
    yield calls
    dns_discovery.reset_dns_driver_for_tests()


def _ask(user):
    org = Organization.objects.create(name="a", slug=f"a-{uuid.uuid4().hex[:6]}")
    info = SimpleNamespace(context=SimpleNamespace(user=user, request=SimpleNamespace(user=user)))
    with tenant_context(TenantContext(organization_id=org.id)):
        q = ClustersQuery()
        return q.astrolift_dns_zones(info, dns_driver="route53"), q.astrolift_dns_certificates(
            info, dns_driver="route53"
        )


def test_an_org_admin_never_sees_the_platform_accounts_zones_or_certificates(listed, permission_resolver):
    permission_resolver.grant(Permission.PROVIDER_PLUGIN_READ)
    zones, certs = _ask(User.objects.create(username=f"admin-{uuid.uuid4().hex[:6]}"))

    assert (zones.supported, zones.zones) == (False, [])
    assert (certs.supported, certs.certificates) == (False, [])
    assert listed == []


def test_the_operator_still_gets_both_pickers(listed, permission_resolver):
    permission_resolver.grant(Permission.PROVIDER_PLUGIN_READ)
    zones, certs = _ask(User.objects.create(username=f"root-{uuid.uuid4().hex[:6]}", is_superuser=True))

    assert [z.id for z in zones.zones] == ["ZB"]
    assert [c.arn for c in certs.certificates] == ["arn:aws:acm:x:1:certificate/b"]
