"""Tests for AWS Route53 DnsDriver (#31)."""

from __future__ import annotations

import pytest

from aws._errors import NotFoundError
from aws.dns_route53 import Route53Config, Route53Driver


@pytest.fixture
def driver_with_zone(route53_client) -> tuple[Route53Driver, str]:
    """Pre-create a hosted zone for tests."""
    response = route53_client.create_hosted_zone(
        Name="acme.platform.example.",
        CallerReference="test",
    )
    zone_id = response["HostedZone"]["Id"].rsplit("/", 1)[-1]
    driver = Route53Driver(
        config=Route53Config(),
        client=route53_client,
    )
    return driver, zone_id


def test_ensure_record_creates(driver_with_zone, route53_client) -> None:
    driver, _ = driver_with_zone
    record = driver.ensure_record(
        zone="acme.platform.example",
        name="api",
        type="A",
        value="192.0.2.1",
    )
    assert record.name == "api"
    assert record.value == "192.0.2.1"


def test_ensure_record_apex(driver_with_zone, route53_client) -> None:
    """Apex record uses '@' or empty name."""
    driver, _ = driver_with_zone
    record = driver.ensure_record(
        zone="acme.platform.example",
        name="@",
        type="TXT",
        value='"verification=abc"',
    )
    assert record.type == "TXT"


def test_ensure_record_idempotent(driver_with_zone) -> None:
    """Re-applying same record is a no-op (UPSERT)."""
    driver, _ = driver_with_zone
    driver.ensure_record(
        zone="acme.platform.example",
        name="api", type="A", value="192.0.2.1",
    )
    driver.ensure_record(
        zone="acme.platform.example",
        name="api", type="A", value="192.0.2.1",
    )


def test_ensure_record_overwrites_with_new_value(
    driver_with_zone, route53_client,
) -> None:
    """Same (name, type) with different value updates."""
    driver, zone_id = driver_with_zone
    driver.ensure_record(
        zone="acme.platform.example",
        name="api", type="A", value="192.0.2.1",
    )
    driver.ensure_record(
        zone="acme.platform.example",
        name="api", type="A", value="192.0.2.99",
    )
    response = route53_client.list_resource_record_sets(
        HostedZoneId=zone_id,
    )
    api_records = [
        rs for rs in response["ResourceRecordSets"]
        if rs.get("Name", "").startswith("api.acme.platform.example")
    ]
    assert len(api_records) == 1
    values = [r["Value"] for r in api_records[0]["ResourceRecords"]]
    assert values == ["192.0.2.99"]


def test_zone_not_found_raises(route53_client) -> None:
    """Unknown zone surfaces as NotFoundError."""
    driver = Route53Driver(client=route53_client)
    with pytest.raises(NotFoundError):
        driver.ensure_record(
            zone="never-existed.example",
            name="api", type="A", value="192.0.2.1",
        )


def test_delete_record(driver_with_zone, route53_client) -> None:
    driver, zone_id = driver_with_zone
    driver.ensure_record(
        zone="acme.platform.example",
        name="api", type="A", value="192.0.2.1",
    )
    driver.delete_record(
        zone="acme.platform.example", name="api", type="A",
    )
    response = route53_client.list_resource_record_sets(
        HostedZoneId=zone_id,
    )
    api_records = [
        rs for rs in response["ResourceRecordSets"]
        if rs.get("Name", "").startswith("api.acme.platform.example")
    ]
    assert api_records == []


def test_delete_record_not_found(driver_with_zone) -> None:
    driver, _ = driver_with_zone
    with pytest.raises(NotFoundError):
        driver.delete_record(
            zone="acme.platform.example",
            name="never-existed", type="A",
        )


def test_list_records(driver_with_zone) -> None:
    driver, _ = driver_with_zone
    driver.ensure_record(
        zone="acme.platform.example",
        name="api", type="A", value="192.0.2.1",
    )
    driver.ensure_record(
        zone="acme.platform.example",
        name="worker", type="A", value="192.0.2.2",
    )
    records = driver.list_records("acme.platform.example")
    api_record = next((r for r in records if r.name == "api"), None)
    worker_record = next((r for r in records if r.name == "worker"), None)
    assert api_record is not None
    assert worker_record is not None


def test_zone_resolution_cached(driver_with_zone) -> None:
    """Repeated calls don't re-list hosted zones."""
    driver, _ = driver_with_zone
    driver.ensure_record(
        zone="acme.platform.example",
        name="a", type="A", value="192.0.2.1",
    )
    driver.ensure_record(
        zone="acme.platform.example",
        name="b", type="A", value="192.0.2.2",
    )
    # Cache populated after first call
    assert "acme.platform.example." in driver._zone_cache
