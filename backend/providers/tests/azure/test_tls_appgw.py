"""Tests for AzureAppGatewayTlsDriver (#44)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from azure._errors import NotFoundError
from azure.tls_appgw import AppGatewayTlsConfig, AzureAppGatewayTlsDriver


class _NotFound(Exception):
    pass


@dataclass
class FakeCertProperties:
    not_before: str | None = "2026-01-01"
    expires_on: str | None = "2027-01-01"


@dataclass
class FakeCert:
    name: str
    properties: FakeCertProperties = field(default_factory=FakeCertProperties)


class FakeCertClient:
    def __init__(self) -> None:
        self.certs: dict[str, FakeCert] = {}

    def get_certificate(self, name: str) -> FakeCert:
        if name not in self.certs:
            raise _NotFound(name)
        return self.certs[name]

    def begin_delete_certificate(self, name: str) -> Any:
        if name not in self.certs:
            raise _NotFound(name)
        del self.certs[name]
        return type("P", (), {"result": lambda self: None})()


@pytest.fixture
def fake_cert_client() -> FakeCertClient:
    _NotFound.__name__ = "ResourceNotFoundError"
    return FakeCertClient()


@pytest.fixture
def driver(fake_cert_client: FakeCertClient) -> AzureAppGatewayTlsDriver:
    return AzureAppGatewayTlsDriver(
        config=AppGatewayTlsConfig(
            subscription_id="sub-1",
            resource_group="rg",
            vault_url="https://vault.azure.net",
            cert_client=fake_cert_client,
        ),
    )


def test_managed_strategy_returns_pending(
    driver: AzureAppGatewayTlsDriver,
) -> None:
    cert = driver.ensure_certificate(
        domain="api.example.com", strategy="azure_managed_cert",
    )
    assert cert.status == "pending"
    assert cert.strategy == "azure_managed_cert"


def test_akv_referenced_returns_pending_when_missing(
    driver: AzureAppGatewayTlsDriver,
) -> None:
    cert = driver.ensure_certificate(
        domain="api.example.com", strategy="akv_referenced",
    )
    assert cert.status == "pending"


def test_akv_referenced_returns_issued_when_present(
    driver: AzureAppGatewayTlsDriver, fake_cert_client: FakeCertClient,
) -> None:
    fake_cert_client.certs["astrolift-api-example-com"] = FakeCert(
        name="astrolift-api-example-com",
    )
    cert = driver.ensure_certificate(
        domain="api.example.com", strategy="akv_referenced",
    )
    assert cert.status == "issued"
    assert cert.not_after == "2027-01-01"


def test_unknown_strategy_rejected(
    driver: AzureAppGatewayTlsDriver,
) -> None:
    with pytest.raises(ValueError, match="unknown strategy"):
        driver.ensure_certificate(domain="x.com", strategy="invalid")


def test_get_certificate_not_found(
    driver: AzureAppGatewayTlsDriver,
) -> None:
    with pytest.raises(NotFoundError):
        driver.get_certificate("never")


def test_revoke_deletes(
    driver: AzureAppGatewayTlsDriver, fake_cert_client: FakeCertClient,
) -> None:
    fake_cert_client.certs["astrolift-x"] = FakeCert(name="astrolift-x")
    driver.revoke_certificate("astrolift-x")
    assert fake_cert_client.certs == {}


def test_revoke_missing_raises(
    driver: AzureAppGatewayTlsDriver,
) -> None:
    with pytest.raises(NotFoundError):
        driver.revoke_certificate("never")


def test_cert_name_canonicalization() -> None:
    driver = AzureAppGatewayTlsDriver(
        config=AppGatewayTlsConfig(
            subscription_id="s",
            resource_group="rg",
            vault_url="v",
        ),
    )
    name = driver._cert_name(domain="API.Example.COM")
    assert name.startswith("astrolift-api-example-com")
    name2 = driver._cert_name(domain="*.dev.example.com")
    assert "*" not in name2
    assert "--" not in name2
