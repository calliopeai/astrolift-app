"""Tests for AzureFederatedIdentityDriver (#45)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from azure._errors import NotFoundError
from azure.identity_federated import (
    AzureFederatedIdentityDriver,
    FederatedIdentityConfig,
)


class _NotFound(Exception):
    pass


@dataclass
class FakeUAI:
    name: str
    client_id: str = "client-1234"


@dataclass
class FakeUAIs:
    identities: dict[str, FakeUAI] = field(default_factory=dict)

    def create_or_update(
        self, *, resource_group_name: str, resource_name: str,
        parameters: dict[str, Any],
    ) -> FakeUAI:
        sa = FakeUAI(name=resource_name)
        self.identities[resource_name] = sa
        return sa

    def get(
        self, *, resource_group_name: str, resource_name: str,
    ) -> FakeUAI:
        if resource_name not in self.identities:
            raise _NotFound(resource_name)
        return self.identities[resource_name]

    def delete(
        self, *, resource_group_name: str, resource_name: str,
    ) -> None:
        if resource_name not in self.identities:
            raise _NotFound(resource_name)
        del self.identities[resource_name]


@dataclass
class FakeFederatedCredentials:
    creds: list[dict[str, Any]] = field(default_factory=list)

    def create_or_update(
        self, *,
        resource_group_name: str, resource_name: str,
        federated_identity_credential_resource_name: str,
        parameters: dict[str, Any],
    ) -> Any:
        self.creds.append({
            "identity": resource_name,
            "credential_name": federated_identity_credential_resource_name,
            "parameters": parameters,
        })


@dataclass
class FakeMSI:
    user_assigned_identities: FakeUAIs = field(default_factory=FakeUAIs)
    federated_identity_credentials: FakeFederatedCredentials = field(
        default_factory=FakeFederatedCredentials,
    )


@pytest.fixture
def fake_msi() -> FakeMSI:
    _NotFound.__name__ = "ResourceNotFoundError"
    return FakeMSI()


@pytest.fixture
def driver(fake_msi: FakeMSI) -> AzureFederatedIdentityDriver:
    return AzureFederatedIdentityDriver(
        config=FederatedIdentityConfig(
            tenant_id="tenant-1",
            subscription_id="sub-1",
            resource_group="rg",
            cluster_oidc_issuer=(
                "https://oidc.prod.azure.com/abc"
            ),
            msi_client=fake_msi,
        ),
    )


def test_create_identity_role(
    driver: AzureFederatedIdentityDriver, fake_msi: FakeMSI,
) -> None:
    client_id = driver.create_identity_role("api", permissions=[])
    assert client_id == "client-1234"
    assert "api" in fake_msi.user_assigned_identities.identities


def test_bind_emits_workload_identity_annotation(
    driver: AzureFederatedIdentityDriver, fake_msi: FakeMSI,
) -> None:
    driver.create_identity_role("api", permissions=[])
    annos = driver.bind_service_account(
        cluster="aks-prod", namespace="acme-api",
        sa_name="api-sa", identity_role="api",
    )
    assert annos["azure.workload.identity/client-id"] == "client-1234"
    assert annos["azure.workload.identity/tenant-id"] == "tenant-1"


def test_bind_creates_federated_credential(
    driver: AzureFederatedIdentityDriver, fake_msi: FakeMSI,
) -> None:
    driver.create_identity_role("api", permissions=[])
    driver.bind_service_account(
        cluster="aks-prod", namespace="ns",
        sa_name="sa", identity_role="api",
    )
    creds = fake_msi.federated_identity_credentials.creds
    assert len(creds) == 1
    cred = creds[0]
    assert cred["identity"] == "api"
    props = cred["parameters"]["properties"]
    assert props["subject"] == "system:serviceaccount:ns:sa"
    assert props["audiences"] == ["api://AzureADTokenExchange"]
    assert props["issuer"] == "https://oidc.prod.azure.com/abc"


def test_bind_without_msi_client_raises(
) -> None:
    driver = AzureFederatedIdentityDriver(
        config=FederatedIdentityConfig(
            tenant_id="t", subscription_id="s", resource_group="rg",
            cluster_oidc_issuer="i", msi_client=None,
        ),
    )
    with pytest.raises(RuntimeError, match="msi_client"):
        driver.bind_service_account(
            cluster="c", namespace="n", sa_name="s", identity_role="r",
        )


def test_attach_policy_is_noop(
    driver: AzureFederatedIdentityDriver,
) -> None:
    # Returns None and doesn't raise — operator wires role
    # assignments out of band.
    assert driver.attach_policy("api", "Reader") is None


def test_delete_identity_removes_uai(
    driver: AzureFederatedIdentityDriver, fake_msi: FakeMSI,
) -> None:
    driver.create_identity_role("doomed", permissions=[])
    driver.delete_identity_role("doomed")
    assert "doomed" not in fake_msi.user_assigned_identities.identities


def test_delete_missing_raises_not_found(
    driver: AzureFederatedIdentityDriver,
) -> None:
    with pytest.raises(NotFoundError):
        driver.delete_identity_role("never")
