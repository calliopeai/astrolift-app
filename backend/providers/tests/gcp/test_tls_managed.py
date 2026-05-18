"""Tests for GCPManagedCertDriver (#38)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from gcp._errors import NotFoundError
from gcp.tls_managed import GCPManagedCertDriver, ManagedCertConfig


class _NotFound(Exception):
    pass


class _AlreadyExists(Exception):
    pass


@dataclass
class FakeManagedSslCertificate:
    domains: list[str]
    status: str = "ACTIVE"


@dataclass
class FakeSslCertificate:
    name: str
    description: str = ""
    type_: str = "MANAGED"
    managed: FakeManagedSslCertificate | None = None


@dataclass
class FakeOperation:
    def result(self) -> None:
        return None


@dataclass
class FakeComputeClient:
    certs: dict[str, FakeSslCertificate] = field(default_factory=dict)

    def insert(
        self, *, project: str, ssl_certificate_resource: Any,
    ) -> FakeOperation:
        if ssl_certificate_resource.name in self.certs:
            raise _AlreadyExists(ssl_certificate_resource.name)
        self.certs[ssl_certificate_resource.name] = ssl_certificate_resource
        return FakeOperation()

    def get(self, *, project: str, ssl_certificate: str) -> Any:
        if ssl_certificate not in self.certs:
            raise _NotFound(ssl_certificate)
        return self.certs[ssl_certificate]

    def delete(
        self, *, project: str, ssl_certificate: str,
    ) -> FakeOperation:
        if ssl_certificate not in self.certs:
            raise _NotFound(ssl_certificate)
        del self.certs[ssl_certificate]
        return FakeOperation()


@pytest.fixture(autouse=True)
def patch_compute_module() -> Any:
    """Driver imports SslCertificate inside ensure_certificate.
    Stub that import path so we don't need google-cloud-compute."""
    import sys
    import types

    fake_module = types.ModuleType("google.cloud.compute_v1")

    class _SslCertificateStub:
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    class _ManagedStub:
        def __init__(self, *, domains: list[str]) -> None:
            self.domains = domains

    fake_module.SslCertificate = _SslCertificateStub
    fake_module.SslCertificateManagedSslCertificate = _ManagedStub
    fake_module.SslCertificatesClient = lambda: None

    google_module = sys.modules.get("google", types.ModuleType("google"))
    cloud_module = sys.modules.get(
        "google.cloud", types.ModuleType("google.cloud"),
    )
    if not hasattr(google_module, "cloud"):
        google_module.cloud = cloud_module
    sys.modules.setdefault("google", google_module)
    sys.modules["google.cloud"] = cloud_module
    sys.modules["google.cloud.compute_v1"] = fake_module
    cloud_module.compute_v1 = fake_module
    yield
    sys.modules.pop("google.cloud.compute_v1", None)


@pytest.fixture
def fake_client() -> FakeComputeClient:
    _NotFound.__name__ = "NotFound"
    _AlreadyExists.__name__ = "AlreadyExists"
    return FakeComputeClient()


@pytest.fixture
def driver(fake_client: FakeComputeClient) -> GCPManagedCertDriver:
    return GCPManagedCertDriver(
        config=ManagedCertConfig(project_id="p", client=fake_client),
    )


def test_ensure_certificate_creates_managed_ssl(
    driver: GCPManagedCertDriver, fake_client: FakeComputeClient,
) -> None:
    cert = driver.ensure_certificate(
        domain="api.example.com",
        sans=["www.example.com"],
        strategy="gcp_managed_cert",
    )
    assert cert.domain == "api.example.com"
    # The fake client recorded an insert
    assert any(
        c.name.startswith("astrolift-")
        for c in fake_client.certs.values()
    )


def test_ensure_certificate_idempotent_on_already_exists(
    driver: GCPManagedCertDriver,
) -> None:
    driver.ensure_certificate(
        domain="api.example.com", strategy="gcp_managed_cert",
    )
    # Second call shouldn't blow up
    cert = driver.ensure_certificate(
        domain="api.example.com", strategy="gcp_managed_cert",
    )
    assert cert.domain == "api.example.com"


def test_unknown_strategy_rejected(
    driver: GCPManagedCertDriver,
) -> None:
    with pytest.raises(ValueError, match="unknown strategy"):
        driver.ensure_certificate(
            domain="x.com", strategy="invalid",
        )


def test_get_certificate_not_found(
    driver: GCPManagedCertDriver,
) -> None:
    with pytest.raises(NotFoundError):
        driver.get_certificate("nonexistent")


def test_revoke_deletes_cert(
    driver: GCPManagedCertDriver, fake_client: FakeComputeClient,
) -> None:
    driver.ensure_certificate(
        domain="api.example.com", strategy="gcp_managed_cert",
    )
    name = list(fake_client.certs.keys())[0]
    driver.revoke_certificate(name)
    assert fake_client.certs == {}


def test_revoke_missing_cert_raises_not_found(
    driver: GCPManagedCertDriver,
) -> None:
    with pytest.raises(NotFoundError):
        driver.revoke_certificate("nonexistent")


def test_cert_name_canonicalization() -> None:
    driver = GCPManagedCertDriver(
        config=ManagedCertConfig(
            project_id="p",
            client=FakeComputeClient(),
        ),
    )
    assert driver._cert_name(domain="API.Example.COM").startswith("astrolift-api-example-com")
    # Wildcard chars normalized
    name = driver._cert_name(domain="*.dev.example.com")
    assert "*" not in name
    assert "--" not in name
