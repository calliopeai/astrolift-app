"""A provisioning run adopts the hosted zone the platform already created (#2389).

CONFLICT's astro.conflict.cloud and conflict.sh were provisioned before #1931,
so their rows record no zone id. Revalidate restarted their runs, which called
``provision_zone``; it refuses a name that is taken, so both runs ended
``failed`` at ``provision_dns_zone`` although the platform had made both zones.

The real Route53 driver runs on moto; the row is in Postgres.
"""

from __future__ import annotations

import boto3
import pytest
from aws.dns_route53 import Route53Config, Route53Driver
from moto import mock_aws

from astrolift_clusters.dns_layout import ZoneRegistrationStep
from astrolift_clusters.models import ManagedDomain
from astrolift_workflows.activities import provision_managed_domain as pmd

ZONE = "adopt2389.example"

pytestmark = pytest.mark.django_db


@pytest.fixture
def route53():
    with mock_aws():
        yield boto3.client("route53", region_name="us-east-1")


def test_a_row_without_a_zone_id_adopts_the_zone_the_platform_created(route53, monkeypatch, cluster, org):
    zone_id = Route53Driver(config=Route53Config(), client=route53).provision_zone(ZONE)["zone_id"]
    nameservers = route53.get_hosted_zone(Id=zone_id)["DelegationSet"]["NameServers"]
    row = ManagedDomain.objects.create(
        organization=org,
        zone=ZONE,
        dns_driver="route53",
        is_wildcard_managed=True,
        provision_state="failed",
        provision_failed_step="provision_dns_zone",
    )
    driver = Route53Driver(config=Route53Config(), client=route53)
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda c, cap: driver)

    result = pmd._provision_dns_zone_sync(cluster.pk, ZONE)

    assert result == {"zone_id": zone_id, "nameservers": nameservers}
    row.refresh_from_db()
    assert row.provision_state == ZoneRegistrationStep.VALIDATE_NS_DELEGATION.value
    assert row.provision_zone_id == zone_id
    assert row.provision_nameservers == nameservers
    zones = route53.list_hosted_zones_by_name(DNSName=f"{ZONE}.")["HostedZones"]
    assert [z["Id"].rsplit("/", 1)[-1] for z in zones if z["Name"] == f"{ZONE}."] == [zone_id]
