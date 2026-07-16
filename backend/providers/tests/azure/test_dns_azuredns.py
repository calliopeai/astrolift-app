"""Tests for AzureDNSDriver (#44)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from azure._errors import NotFoundError
from azure.dns_azuredns import AzureDNSConfig, AzureDNSDriver


class _NotFound(Exception):
    pass


@dataclass
class FakeRecord:
    name: str
    type: str
    ttl: int = 300
    a_records: list[Any] = field(default_factory=list)
    cname_record: Any = None


@dataclass
class FakeRecordSets:
    sets: dict[tuple[str, str, str], dict[str, Any]] = field(
        default_factory=dict,
    )

    def create_or_update(
        self,
        *,
        resource_group_name: str,
        zone_name: str,
        relative_record_set_name: str,
        record_type: str,
        parameters: dict[str, Any],
    ) -> Any:
        key = (zone_name, relative_record_set_name, record_type)
        self.sets[key] = parameters

    def delete(
        self,
        *,
        resource_group_name: str,
        zone_name: str,
        relative_record_set_name: str,
        record_type: str,
    ) -> None:
        key = (zone_name, relative_record_set_name, record_type)
        if key not in self.sets:
            raise _NotFound(str(key))
        del self.sets[key]

    def list_by_dns_zone(
        self,
        *,
        resource_group_name: str,
        zone_name: str,
    ) -> list[Any]:
        results = []
        for (zname, rname, rtype), params in self.sets.items():
            if zname != zone_name:
                continue
            rec = FakeRecord(
                name=rname,
                type=f"Microsoft.Network/dnszones/{rtype}",
                ttl=params.get("ttl", 300),
            )
            if rtype == "A":
                rec.a_records = [
                    type("R", (), {"ipv4_address": v["ipv4_address"]})() for v in params.get("a_records", [])
                ]
            elif rtype == "CNAME":
                cname_data = params.get("cname_record", {})
                rec.cname_record = type(
                    "C",
                    (),
                    {"cname": cname_data.get("cname", "")},
                )()
            results.append(rec)
        return results


@dataclass
class FakeDNSClient:
    record_sets: FakeRecordSets = field(default_factory=FakeRecordSets)


@pytest.fixture
def fake_client() -> FakeDNSClient:
    _NotFound.__name__ = "ResourceNotFoundError"
    return FakeDNSClient()


@pytest.fixture
def driver(fake_client: FakeDNSClient) -> AzureDNSDriver:
    return AzureDNSDriver(
        config=AzureDNSConfig(
            subscription_id="sub-1",
            resource_group="rg",
            client=fake_client,
        ),
    )


def test_ensure_record_creates_a(
    driver: AzureDNSDriver,
    fake_client: FakeDNSClient,
) -> None:
    record = driver.ensure_record(
        zone="example.com",
        name="api",
        type="A",
        value="1.2.3.4",
    )
    assert record.value == "1.2.3.4"
    assert ("example.com", "api", "A") in fake_client.record_sets.sets


def test_ensure_record_creates_cname(
    driver: AzureDNSDriver,
    fake_client: FakeDNSClient,
) -> None:
    driver.ensure_record(
        zone="example.com",
        name="www",
        type="CNAME",
        value="acme.com",
    )
    params = fake_client.record_sets.sets[("example.com", "www", "CNAME")]
    assert params["cname_record"]["cname"] == "acme.com"


def test_unsupported_record_type_rejected(driver: AzureDNSDriver) -> None:
    with pytest.raises(ValueError, match="unsupported record type"):
        driver.ensure_record(
            zone="example.com",
            name="x",
            type="CAA",
            value="anything",
        )


def test_relative_name_strips_zone(driver: AzureDNSDriver) -> None:
    assert driver._relative_name(name="api.example.com", zone="example.com") == "api"
    assert driver._relative_name(name="@", zone="example.com") == "@"
    assert driver._relative_name(name="standalone", zone="example.com") == "standalone"


def test_apex_uses_at(
    driver: AzureDNSDriver,
    fake_client: FakeDNSClient,
) -> None:
    driver.ensure_record(
        zone="example.com",
        name="@",
        type="A",
        value="1.1.1.1",
    )
    assert ("example.com", "@", "A") in fake_client.record_sets.sets


def test_delete_removes_record(
    driver: AzureDNSDriver,
    fake_client: FakeDNSClient,
) -> None:
    driver.ensure_record(
        zone="example.com",
        name="api",
        type="A",
        value="1.2.3.4",
    )
    driver.delete_record(zone="example.com", name="api", type="A")
    assert ("example.com", "api", "A") not in fake_client.record_sets.sets


def test_delete_missing_raises_not_found(driver: AzureDNSDriver) -> None:
    with pytest.raises(NotFoundError):
        driver.delete_record(zone="example.com", name="never", type="A")


def test_list_records(
    driver: AzureDNSDriver,
) -> None:
    driver.ensure_record(
        zone="example.com",
        name="api",
        type="A",
        value="1.2.3.4",
    )
    driver.ensure_record(
        zone="example.com",
        name="www",
        type="CNAME",
        value="acme.com",
    )
    records = driver.list_records("example.com")
    assert len(records) == 2
    by_type = {r.type for r in records}
    assert by_type == {"A", "CNAME"}
