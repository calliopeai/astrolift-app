"""Tests for AWS Route53 DnsDriver (#31)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aws._errors import NotFoundError, ThrottledError, TransientError
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


def test_zone_exists_reports_a_name_already_in_the_account(driver_with_zone) -> None:
    driver, _ = driver_with_zone
    assert driver.zone_exists("acme.platform.example") is True
    assert driver.zone_exists("nobody-has-this.example") is False


def test_zone_exists_is_insensitive_to_a_trailing_dot(driver_with_zone) -> None:
    driver, _ = driver_with_zone
    assert driver.zone_exists("acme.platform.example.") is True


# ---- #2389: a run with no recorded zone id adopts the zone the platform made --


def _hosted_zones_named(route53_client, zone: str) -> list[str]:
    zones = route53_client.list_hosted_zones_by_name(DNSName=f"{zone}.")["HostedZones"]
    return [z["Id"].rsplit("/", 1)[-1] for z in zones if z["Name"] == f"{zone}."]


def test_a_zone_provision_zone_created_is_adopted_with_its_nameservers(route53_client) -> None:
    created = Route53Driver(config=Route53Config(), client=route53_client).provision_zone("shop.example")
    nameservers = route53_client.get_hosted_zone(Id=created["zone_id"])["DelegationSet"]["NameServers"]

    adopted = Route53Driver(config=Route53Config(), client=route53_client).adopt_platform_zone("shop.example")

    assert nameservers
    assert adopted == {"zone_id": created["zone_id"], "nameservers": nameservers}
    assert _hosted_zones_named(route53_client, "shop.example") == [created["zone_id"]]


def test_a_zone_with_a_foreign_caller_reference_is_not_adopted(route53_client) -> None:
    from aws._errors import ConflictError

    route53_client.create_hosted_zone(Name="corp.example.", CallerReference="someone-else")
    driver = Route53Driver(config=Route53Config(), client=route53_client)

    assert driver.adopt_platform_zone("corp.example") is None
    with pytest.raises(ConflictError):
        driver.provision_zone("corp.example")


def test_two_zones_with_the_name_are_not_adopted(route53_client) -> None:
    """Even when both carry the platform's CallerReference: by name, either one could be meant."""
    driver = Route53Driver(config=Route53Config(), client=route53_client)
    driver.provision_zone("shop.example")
    route53_client.create_hosted_zone(Name="shop.example.", CallerReference="astrolift-shop.example-1")

    assert Route53Driver(config=Route53Config(), client=route53_client).adopt_platform_zone("shop.example") is None


def test_an_absent_zone_is_not_adopted_and_nothing_is_created(route53_client) -> None:
    driver = Route53Driver(config=Route53Config(), client=route53_client)

    assert driver.adopt_platform_zone("nobody-has-this.example") is None
    assert _hosted_zones_named(route53_client, "nobody-has-this.example") == []


# ---- #2519: every validation record, once ACM has published it ------------

_ZONE = "acme.platform.example"
_SANS = [_ZONE, f"*.{_ZONE}", f"*.pr.{_ZONE}"]
_ARN = "arn:aws:acm:us-east-1:123456789012:certificate/0f3c2f1e-8d1b-4c3a-9a51-2c0e5b7d9e10"
# ACM validates an apex and its wildcard through one CNAME.
_APEX = {"Name": f"_a1.{_ZONE}.", "Type": "CNAME", "Value": "_b1.acm-validations.aws."}
_PREVIEW = {"Name": f"_c2.pr.{_ZONE}.", "Type": "CNAME", "Value": "_d2.acm-validations.aws."}
_RECORDS = {_ZONE: _APEX, f"*.{_ZONE}": _APEX, f"*.pr.{_ZONE}": _PREVIEW}


class _Acm:
    """ACM after ``RequestCertificate``: it lists every domain at once and fills in
    their ``ResourceRecord`` later. Each describe publishes the next stage of
    ``published`` (the domains whose record is out); the last stage repeats."""

    def __init__(self, published, *, status="PENDING_VALIDATION", describe_error=None):
        self._published = list(published)
        self._status = status
        self._describe_error = describe_error
        self.describes = 0
        self.deleted: list[str] = []

    def request_certificate(self, **kwargs):
        return {"CertificateArn": _ARN}

    def describe_certificate(self, **kwargs):
        assert kwargs == {"CertificateArn": _ARN}
        self.describes += 1
        if self._describe_error is not None:
            raise self._describe_error
        published = self._published.pop(0) if len(self._published) > 1 else self._published[0]
        options = []
        for domain, record in _RECORDS.items():
            option = {"DomainName": domain, "ValidationMethod": "DNS"}
            if domain in published:
                option["ResourceRecord"] = record
            options.append(option)
        return {"Certificate": {"CertificateArn": _ARN, "Status": self._status, "DomainValidationOptions": options}}

    def delete_certificate(self, **kwargs):
        self.deleted.append(kwargs["CertificateArn"])


@pytest.fixture
def clock(monkeypatch) -> list[float]:
    """The driver's clock, faked: each pause advances ``monotonic`` and is recorded."""
    import aws.dns_route53 as module

    now = [0.0]
    pauses: list[float] = []

    def sleep(seconds: float) -> None:
        pauses.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=sleep, time=module.time.time))
    return pauses


def _driver(acm: _Acm) -> Route53Driver:
    driver = Route53Driver(client=None)
    driver._acm = acm
    return driver


def _record(record: dict[str, str]) -> dict[str, str]:
    return {"name": record["Name"], "type": record["Type"], "value": record["Value"]}


def test_request_wildcard_cert_waits_until_acm_has_published_every_record(clock) -> None:
    """One read a second after the request found none of the records, or some:
    the ones never written kept the certificate pending until it expired."""
    acm = _Acm([(), (), (_ZONE, f"*.{_ZONE}"), tuple(_RECORDS)])

    result = _driver(acm).request_wildcard_cert(_ZONE, "Z1", sans=_SANS)

    assert result == {"cert_id": _ARN, "validation_records": [_record(_APEX), _record(_PREVIEW)]}
    assert acm.describes == 4
    assert sum(clock) <= 60
    assert acm.deleted == []


def test_a_record_the_apex_and_its_wildcard_share_is_listed_once(driver_with_zone, acm_client, clock) -> None:
    """Listed per domain, the shared CNAME was written twice."""
    driver, zone_id = driver_with_zone
    driver._acm = acm_client

    plain = driver.request_wildcard_cert(_ZONE, zone_id)["validation_records"]
    preview = driver.request_wildcard_cert(_ZONE, zone_id, sans=_SANS)["validation_records"]

    assert len(plain) == 1
    assert len(preview) == len({record["name"] for record in preview}) == 2


def test_a_certificate_whose_records_never_all_appear_is_deleted_and_retried(clock) -> None:
    """At the deadline the request fails retriably instead of handing back a
    partial list, and the certificate the caller will never learn of is deleted."""
    acm = _Acm([(_ZONE, f"*.{_ZONE}")])

    with pytest.raises(TransientError, match="every domain"):
        _driver(acm).request_wildcard_cert(_ZONE, "Z1", sans=_SANS)

    assert acm.deleted == [_ARN]
    assert sum(clock) == 60
    assert acm.describes == len(clock)


def test_a_certificate_that_cannot_be_read_back_is_deleted(clock) -> None:
    from botocore.exceptions import ClientError

    throttled = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "DescribeCertificate")
    acm = _Acm([tuple(_RECORDS)], describe_error=throttled)

    with pytest.raises(ThrottledError):
        _driver(acm).request_wildcard_cert(_ZONE, "Z1", sans=_SANS)

    assert acm.deleted == [_ARN]


def test_a_certificate_acm_already_failed_is_returned_for_the_poll_to_report(clock) -> None:
    acm = _Acm([()], status="FAILED")

    assert _driver(acm).request_wildcard_cert(_ZONE, "Z1", sans=_SANS) == {"cert_id": _ARN, "validation_records": []}
    assert acm.describes == 1
    assert acm.deleted == []


def test_poll_cert_status_lists_what_a_pending_certificate_waits_on() -> None:
    """So the caller can write a record its request never wrote."""
    pending = _driver(_Acm([(_ZONE, f"*.{_ZONE}")])).poll_cert_status(_ZONE, _ARN)
    issued = _driver(_Acm([tuple(_RECORDS)], status="ISSUED")).poll_cert_status(_ZONE, _ARN)

    assert pending == {"status": "pending", "cert_arn": None, "validation_records": [_record(_APEX)]}
    assert issued == {"status": "issued", "cert_arn": _ARN}
