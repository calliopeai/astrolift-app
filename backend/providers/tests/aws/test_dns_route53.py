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
        name="api",
        type="A",
        value="192.0.2.1",
    )
    driver.ensure_record(
        zone="acme.platform.example",
        name="api",
        type="A",
        value="192.0.2.1",
    )


def test_ensure_record_overwrites_with_new_value(
    driver_with_zone,
    route53_client,
) -> None:
    """Same (name, type) with different value updates."""
    driver, zone_id = driver_with_zone
    driver.ensure_record(
        zone="acme.platform.example",
        name="api",
        type="A",
        value="192.0.2.1",
    )
    driver.ensure_record(
        zone="acme.platform.example",
        name="api",
        type="A",
        value="192.0.2.99",
    )
    response = route53_client.list_resource_record_sets(
        HostedZoneId=zone_id,
    )
    api_records = [
        rs for rs in response["ResourceRecordSets"] if rs.get("Name", "").startswith("api.acme.platform.example")
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
            name="api",
            type="A",
            value="192.0.2.1",
        )


def test_delete_record(driver_with_zone, route53_client) -> None:
    driver, zone_id = driver_with_zone
    driver.ensure_record(
        zone="acme.platform.example",
        name="api",
        type="A",
        value="192.0.2.1",
    )
    driver.delete_record(
        zone="acme.platform.example",
        name="api",
        type="A",
    )
    response = route53_client.list_resource_record_sets(
        HostedZoneId=zone_id,
    )
    api_records = [
        rs for rs in response["ResourceRecordSets"] if rs.get("Name", "").startswith("api.acme.platform.example")
    ]
    assert api_records == []


def test_delete_record_not_found(driver_with_zone) -> None:
    driver, _ = driver_with_zone
    with pytest.raises(NotFoundError):
        driver.delete_record(
            zone="acme.platform.example",
            name="never-existed",
            type="A",
        )


def test_list_records(driver_with_zone) -> None:
    driver, _ = driver_with_zone
    driver.ensure_record(
        zone="acme.platform.example",
        name="api",
        type="A",
        value="192.0.2.1",
    )
    driver.ensure_record(
        zone="acme.platform.example",
        name="worker",
        type="A",
        value="192.0.2.2",
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
        name="a",
        type="A",
        value="192.0.2.1",
    )
    driver.ensure_record(
        zone="acme.platform.example",
        name="b",
        type="A",
        value="192.0.2.2",
    )
    # Cache populated after first call
    assert "acme.platform.example." in driver._zone_cache


# ---- list_zones (#861) --------------------------------------------


def test_list_zones_returns_hosted_zones(route53_client) -> None:
    """list_zones returns every hosted zone with a bare id, FQDN name,
    private flag, and a pre-serialized config_json blob."""
    import json

    route53_client.create_hosted_zone(Name="acme.example.", CallerReference="z1")
    route53_client.create_hosted_zone(Name="beta.example.", CallerReference="z2")
    driver = Route53Driver(client=route53_client)

    zones = driver.list_zones()

    names = {z["name"] for z in zones}
    assert "acme.example." in names
    assert "beta.example." in names

    acme = next(z for z in zones if z["name"] == "acme.example.")
    # Bare hosted-zone id — no "/hostedzone/" prefix.
    assert acme["id"].startswith("Z") or acme["id"]  # moto ids are opaque
    assert "/hostedzone/" not in acme["id"]
    assert acme["private"] is False
    # config_json is drop-in for the dialog textarea: zone_id filled,
    # certificate_arn left blank for the cert picker to populate.
    parsed = json.loads(acme["config_json"])
    assert parsed == {"zone_id": acme["id"], "certificate_arn": ""}


def test_list_zones_empty_when_none(route53_client) -> None:
    """No hosted zones → empty list (not an error)."""
    driver = Route53Driver(client=route53_client)
    assert driver.list_zones() == []


# ---- list_certificates (#858) -------------------------------------


def test_list_certificates_returns_issued() -> None:
    """list_certificates surfaces ISSUED ACM certs with arn + domain.

    Uses a stub ACM client rather than moto: moto leaves
    ``request_certificate`` certs at PENDING_VALIDATION (correct
    real-AWS behavior), so they'd never pass the driver's ISSUED-only
    filter. The empty-list path below is moto-backed.
    """
    from unittest.mock import MagicMock

    arn = "arn:aws:acm:us-east-1:123456789012:certificate/abc"

    class _Paginator:
        def paginate(self, **kwargs):
            assert kwargs.get("CertificateStatuses") == ["ISSUED"]
            return [
                {
                    "CertificateSummaryList": [
                        {"CertificateArn": arn, "DomainName": "api.acme.example", "Status": "ISSUED"},
                    ]
                }
            ]

    acm = MagicMock()
    acm.get_paginator.return_value = _Paginator()
    acm.describe_certificate.return_value = {
        "Certificate": {"DomainName": "api.acme.example", "Status": "ISSUED"},
    }

    driver = Route53Driver(client=None)
    driver._acm = acm

    certs = driver.list_certificates()

    match = next((c for c in certs if c["arn"] == arn), None)
    assert match is not None, f"expected cert {arn} in {certs}"
    assert match["domain_name"] == "api.acme.example"
    assert match["status"] == "ISSUED"
    assert match["name"]  # human label is non-empty


def test_list_certificates_empty_when_none(acm_client) -> None:
    """No certs → empty list (moto, no certs requested)."""
    driver = Route53Driver(client=None)
    driver._acm = acm_client
    assert driver.list_certificates() == []


# ---- wildcard cert SANs (#70) ----------------------------------------


def test_request_wildcard_cert_requests_the_caller_sans(driver_with_zone, acm_client) -> None:
    """The caller's SAN list is what ACM is asked for. ``*.<zone>`` matches one
    label only, so a preview wildcard two labels deep has to be named."""
    driver, zone_id = driver_with_zone
    driver._acm = acm_client

    result = driver.request_wildcard_cert(
        "acme.platform.example",
        zone_id,
        sans=[
            "acme.platform.example",
            "*.acme.platform.example",
            "*.pr.acme.platform.example",
        ],
    )

    desc = acm_client.describe_certificate(CertificateArn=result["cert_id"])
    sans = desc["Certificate"]["SubjectAlternativeNames"]
    assert "*.pr.acme.platform.example" in sans


def test_request_wildcard_cert_defaults_without_sans(driver_with_zone, acm_client) -> None:
    """No SAN list → the driver's own zone + one-label wildcard."""
    driver, zone_id = driver_with_zone
    driver._acm = acm_client

    result = driver.request_wildcard_cert("acme.platform.example", zone_id)

    desc = acm_client.describe_certificate(CertificateArn=result["cert_id"])
    sans = desc["Certificate"]["SubjectAlternativeNames"]
    assert "acme.platform.example" in sans
    assert "*.acme.platform.example" in sans
    assert "*.pr.acme.platform.example" not in sans


# ---- #1931: zones by id, never by guess -----------------------------------


def test_provision_zone_refuses_a_name_that_already_exists(driver_with_zone, route53_client) -> None:
    from aws._errors import ConflictError

    driver, _ = driver_with_zone
    with pytest.raises(ConflictError):
        driver.provision_zone("acme.platform.example")
    zones = route53_client.list_hosted_zones_by_name(DNSName="acme.platform.example.")["HostedZones"]
    assert [z["Name"] for z in zones].count("acme.platform.example.") == 1


def test_a_pinned_zone_gets_the_write_even_when_a_twin_exists(driver_with_zone, route53_client) -> None:
    from aws._errors import ConflictError

    driver, original = driver_with_zone
    twin = route53_client.create_hosted_zone(Name="acme.platform.example.", CallerReference="twin")
    twin_id = twin["HostedZone"]["Id"].rsplit("/", 1)[-1]

    with pytest.raises(ConflictError):
        Route53Driver(config=Route53Config(), client=route53_client).ensure_record(
            zone="acme.platform.example", name="api", type="A", value="192.0.2.1"
        )

    driver.pin_zone("acme.platform.example", twin_id)
    driver.ensure_record(zone="acme.platform.example", name="api", type="A", value="192.0.2.9")

    def names(zone_id):
        rows = route53_client.list_resource_record_sets(HostedZoneId=zone_id)["ResourceRecordSets"]
        return {r["Name"] for r in rows if r["Type"] == "A"}

    assert names(twin_id) == {"api.acme.platform.example."}
    assert names(original) == set()


def test_only_a_zone_provision_zone_created_verifies_as_platform_made(route53_client) -> None:
    driver = Route53Driver(config=Route53Config(), client=route53_client)
    ours = driver.provision_zone("shop.example")["zone_id"]
    foreign = route53_client.create_hosted_zone(Name="corp.example.", CallerReference="someone-else")
    foreign_id = foreign["HostedZone"]["Id"].rsplit("/", 1)[-1]

    assert driver.zone_created_by_platform("shop.example", ours) is True
    assert driver.zone_created_by_platform("corp.example", foreign_id) is False
    assert driver.zone_created_by_platform("other.example", ours) is False  # name must match
    assert driver.zone_created_by_platform("shop.example", "ZNOSUCHZONE") is False
