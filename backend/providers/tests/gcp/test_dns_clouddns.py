"""Tests for CloudDNSDriver (#38)."""

from __future__ import annotations

import pytest

from gcp._errors import NotFoundError
from gcp.dns_clouddns import CloudDNSConfig, CloudDNSDriver

from .conftest import FakeDNSClient, FakeManagedZone, FakeRecordSet


@pytest.fixture
def fake_zone() -> FakeManagedZone:
    return FakeManagedZone(
        name="astrolift-prod",
        dns_name="example.com.",
        record_sets=[
            FakeRecordSet(
                name="api.example.com.",
                record_type="A",
                ttl=300,
                rrdatas=["1.2.3.4"],
            ),
        ],
    )


@pytest.fixture
def fake_client(fake_zone: FakeManagedZone) -> FakeDNSClient:
    return FakeDNSClient(project="p", zones=[fake_zone])


@pytest.fixture
def driver(fake_client: FakeDNSClient) -> CloudDNSDriver:
    return CloudDNSDriver(
        config=CloudDNSConfig(project_id="p", client=fake_client),
    )


def test_ensure_record_creates_new(
    driver: CloudDNSDriver,
    fake_zone: FakeManagedZone,
) -> None:
    record = driver.ensure_record(
        zone="example.com",
        name="www",
        type="A",
        value="9.8.7.6",
    )
    assert record.name == "www"
    assert record.value == "9.8.7.6"
    # Was added to the zone
    names = [(rs.name, rs.record_type) for rs in fake_zone.record_sets]
    assert ("www.example.com.", "A") in names


def test_ensure_record_replaces_existing(
    driver: CloudDNSDriver,
    fake_zone: FakeManagedZone,
) -> None:
    """ensure_record is UPSERT: a second call should replace the
    earlier rrset, not stack alongside it."""
    driver.ensure_record(
        zone="example.com",
        name="api",
        type="A",
        value="5.5.5.5",
    )
    apis = [rs for rs in fake_zone.record_sets if rs.name == "api.example.com." and rs.record_type == "A"]
    assert len(apis) == 1
    assert apis[0].rrdatas == ["5.5.5.5"]


def test_delete_record_removes(
    driver: CloudDNSDriver,
    fake_zone: FakeManagedZone,
) -> None:
    driver.delete_record(zone="example.com", name="api", type="A")
    apis = [rs for rs in fake_zone.record_sets if rs.name == "api.example.com." and rs.record_type == "A"]
    assert apis == []


def test_delete_record_raises_not_found_for_missing(
    driver: CloudDNSDriver,
) -> None:
    with pytest.raises(NotFoundError):
        driver.delete_record(zone="example.com", name="never", type="A")


def test_list_records_translates_back(
    driver: CloudDNSDriver,
) -> None:
    records = driver.list_records("example.com")
    assert any(r.name == "api" and r.type == "A" for r in records)


def test_zone_caches_after_first_lookup(
    driver: CloudDNSDriver,
) -> None:
    driver.ensure_record(
        zone="example.com",
        name="x",
        type="A",
        value="1.1.1.1",
    )
    assert "example.com." in driver._config._zone_cache


def test_unknown_zone_raises_not_found(
    driver: CloudDNSDriver,
) -> None:
    with pytest.raises(NotFoundError):
        driver.ensure_record(
            zone="nope.com",
            name="x",
            type="A",
            value="1.1.1.1",
        )


def test_apex_record_uses_zone_fqdn(
    driver: CloudDNSDriver,
    fake_zone: FakeManagedZone,
) -> None:
    driver.ensure_record(
        zone="example.com",
        name="@",
        type="A",
        value="1.1.1.1",
    )
    apex = [rs for rs in fake_zone.record_sets if rs.name == "example.com." and rs.record_type == "A"]
    assert len(apex) == 1
