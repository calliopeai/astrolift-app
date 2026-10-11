"""Native PostgreSQL and authenticated HTTP recovery after certificate registration."""

import pytest

from astrolift_clusters.models import ManagedDomain
from astrolift_clusters.tests.domain_http_helpers_2287 import graphql_http
from astrolift_clusters.tests.test_domain_diagnostics_2287 import world as domain_world
from astrolift_workflows.activities.provision_managed_domain import _register_managed_domain_row_sync

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def world(monkeypatch):
    return domain_world.__wrapped__(monkeypatch)


def test_certificate_registration_retains_cluster_and_existing_domain_recovery(client, world):
    domain = world.domain
    original_guid, original_org = domain.guid, domain.organization_id
    domain.dns_config = {
        "provision_cluster_id": world.cluster.pk,
        "zone_id": "ZEXAMPLE",
        "region": "existing-region",
        "delegation_check": {"passed": False, "reason": "previous check"},
    }
    domain.save(update_fields=["dns_config", "updated_at", "version"])
    original_version = domain.version
    pk = _register_managed_domain_row_sync(world.cluster.pk, domain.zone, "ZEXAMPLE", "cert-original")
    reply = graphql_http(
        client,
        world.headers,
        "query($id:GUID!){astroliftManagedDomain(domainId:$id){id organizationSlug provisionClusterId}}",
        {"id": str(original_guid)},
    )
    assert "errors" not in reply, reply
    assert reply["data"]["astroliftManagedDomain"]["provisionClusterId"] == str(world.cluster.guid)
    domain.refresh_from_db()
    assert domain.pk == pk and domain.guid == original_guid and domain.organization_id == original_org
    assert domain.version > original_version
    assert domain.provision_zone_id == "ZEXAMPLE"
    assert domain.dns_config["region"] == "existing-region"
    assert domain.dns_config["delegation_check"] == {"passed": False, "reason": "previous check"}
    assert domain.dns_config["certificate_arn"] == "cert-original"
    assert _register_managed_domain_row_sync(world.cluster.pk, domain.zone, "ZEXAMPLE", "cert-original") == pk
    assert ManagedDomain.objects.filter(zone=domain.zone).count() == 1
    domain.refresh_from_db()
    assert domain.dns_config["provision_cluster_id"] == world.cluster.pk


def test_legacy_domain_without_cluster_gains_the_actual_registration_cluster(world):
    domain = world.domain
    domain.dns_config = {"zone_id": "ZEXAMPLE"}
    domain.save(update_fields=["dns_config", "updated_at", "version"])
    _register_managed_domain_row_sync(world.cluster.pk, domain.zone, "ZEXAMPLE", "cert-current")
    domain.refresh_from_db()
    assert domain.dns_config["provision_cluster_id"] == world.cluster.pk
