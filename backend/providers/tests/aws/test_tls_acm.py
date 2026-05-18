"""Tests for AWS ACM TlsDriver (#31)."""

from __future__ import annotations

import pytest

from aws._errors import NotFoundError
from aws.tls_acm import ACMConfig, ACMDriver


@pytest.fixture
def driver(acm_client) -> ACMDriver:
    return ACMDriver(
        config=ACMConfig(region="us-east-1"),
        client=acm_client,
    )


def test_ensure_certificate_returns_arn(driver: ACMDriver) -> None:
    cert = driver.ensure_certificate(
        domain="api.acme.platform.example",
    )
    assert cert.id.startswith("arn:aws:acm:us-east-1:")
    assert cert.domain == "api.acme.platform.example"


def test_ensure_certificate_with_sans(driver: ACMDriver) -> None:
    cert = driver.ensure_certificate(
        domain="acme.platform.example",
        sans=["*.acme.platform.example", "*.pr.acme.platform.example"],
    )
    assert "*.acme.platform.example" in cert.sans
    assert "*.pr.acme.platform.example" in cert.sans


def test_ensure_certificate_dns_validation(driver: ACMDriver, acm_client) -> None:
    """ACM driver always uses DNS validation (auto-renew via DNS)."""
    cert = driver.ensure_certificate(domain="api.acme.platform.example")
    raw = acm_client.describe_certificate(CertificateArn=cert.id)
    assert raw["Certificate"]["DomainValidationOptions"][0][
        "ValidationMethod"
    ] == "DNS"


def test_ensure_certificate_unknown_strategy_rejected(driver: ACMDriver) -> None:
    with pytest.raises(ValueError):
        driver.ensure_certificate(
            domain="api.acme.platform.example",
            strategy="weird",
        )


def test_ensure_certificate_letsencrypt_strategy_accepted(driver: ACMDriver) -> None:
    """The SDK protocol's default strategy is 'letsencrypt'.
    ACM driver accepts it (informationally) — the cert is still
    ACM-issued."""
    cert = driver.ensure_certificate(
        domain="api.acme.platform.example",
        strategy="letsencrypt",
    )
    assert cert.id.startswith("arn:aws:acm:")


def test_get_certificate_not_found(driver: ACMDriver) -> None:
    with pytest.raises(NotFoundError):
        driver.get_certificate(
            "arn:aws:acm:us-east-1:123:certificate/00000000-0000-0000-0000-000000000000",
        )


def test_revoke_certificate_not_found(driver: ACMDriver) -> None:
    with pytest.raises(NotFoundError):
        driver.revoke_certificate(
            "arn:aws:acm:us-east-1:123:certificate/00000000-0000-0000-0000-000000000000",
        )


def test_revoke_existing_cert(driver: ACMDriver, acm_client) -> None:
    cert = driver.ensure_certificate(domain="api.acme.platform.example")
    driver.revoke_certificate(cert.id)
    with pytest.raises(NotFoundError):
        driver.get_certificate(cert.id)


def test_validation_cnames_returned(driver: ACMDriver) -> None:
    """The DNS-01 validation needs CNAME records published by
    the operator (or our Route53 driver)."""
    cert = driver.ensure_certificate(domain="api.acme.platform.example")
    cnames = driver.get_validation_cnames(cert.id)
    # moto returns at least one validation record
    assert len(cnames) >= 1
    name, value = cnames[0]
    assert name and value
