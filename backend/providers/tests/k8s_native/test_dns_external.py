"""Tests for k8s_native external-dns DnsDriver (#50)."""

from __future__ import annotations

from unittest.mock import MagicMock

from _sdk.cluster import ApplyResult, DeleteResult
from k8s_native.dns_external import ExternalDnsConfig, ExternalDnsDriver


def test_render_endpoint_basic() -> None:
    driver = ExternalDnsDriver()
    endpoint = driver.render_endpoint(
        zone="acme.example", name="api",
        type="A", value="192.0.2.1",
    )
    assert endpoint["kind"] == "DNSEndpoint"
    assert endpoint["spec"]["endpoints"][0]["dnsName"] == "api.acme.example"
    assert endpoint["spec"]["endpoints"][0]["recordType"] == "A"
    assert endpoint["spec"]["endpoints"][0]["targets"] == ["192.0.2.1"]


def test_render_endpoint_apex() -> None:
    driver = ExternalDnsDriver()
    endpoint = driver.render_endpoint(
        zone="acme.example", name="@",
        type="TXT", value='"verify=abc"',
    )
    assert endpoint["spec"]["endpoints"][0]["dnsName"] == "acme.example"


def test_render_uses_safe_endpoint_name() -> None:
    driver = ExternalDnsDriver()
    endpoint = driver.render_endpoint(
        zone="ACME.example", name="My_API",
        type="A", value="192.0.2.1",
    )
    name = endpoint["metadata"]["name"]
    # k8s-safe: lowercase, no underscores
    assert name == name.lower()
    assert "_" not in name


def test_render_only_mode_no_cluster_driver() -> None:
    """ensure_record returns a Record without applying."""
    driver = ExternalDnsDriver()
    record = driver.ensure_record(
        zone="acme.example", name="api",
        type="A", value="192.0.2.1",
    )
    assert record.name == "api"


def test_ensure_record_applies_when_cluster_driver_bound() -> None:
    cluster_driver = MagicMock()
    cluster_driver.apply_manifests.return_value = ApplyResult(
        created=["DNSEndpoint/acme-example-api-a"],
        updated=[], unchanged=[], errors=[],
    )
    driver = ExternalDnsDriver(
        config=ExternalDnsConfig(cluster_driver=cluster_driver),
    )
    driver.ensure_record(
        zone="acme.example", name="api",
        type="A", value="192.0.2.1",
    )
    cluster_driver.apply_manifests.assert_called_once()


def test_delete_record_calls_delete() -> None:
    cluster_driver = MagicMock()
    cluster_driver.delete_manifests.return_value = DeleteResult(
        deleted=["DNSEndpoint/acme-example-api-a"],
        not_found=[], errors=[],
    )
    driver = ExternalDnsDriver(
        config=ExternalDnsConfig(cluster_driver=cluster_driver),
    )
    driver.delete_record(zone="acme.example", name="api", type="A")
    cluster_driver.delete_manifests.assert_called_once()


def test_list_records_returns_empty() -> None:
    """list_records is intentionally empty — query the DNS
    provider directly via that provider's DnsDriver."""
    driver = ExternalDnsDriver()
    assert driver.list_records("acme.example") == []
