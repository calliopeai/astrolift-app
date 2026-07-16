"""Tests for projected SA token WorkloadIdentityDriver (#51)."""

from __future__ import annotations

import pytest

from k8s_native.identity_projected import (
    ProjectedSaTokenConfig,
    ProjectedSaTokenDriver,
)


def test_create_role_returns_path() -> None:
    driver = ProjectedSaTokenDriver()
    arn = driver.create_identity_role(
        name="acme-api",
        permissions=[{"action": "read", "resource": "secret/*"}],
    )
    assert "acme-api" in arn


def test_bind_unknown_role_raises() -> None:
    driver = ProjectedSaTokenDriver()
    with pytest.raises(KeyError):
        driver.bind_service_account(
            cluster="x",
            namespace="ns",
            sa_name="api",
            identity_role="missing",
        )


def test_bind_returns_annotations() -> None:
    driver = ProjectedSaTokenDriver()
    driver.create_identity_role(name="acme-api", permissions=[])
    annotations = driver.bind_service_account(
        cluster="x",
        namespace="ns",
        sa_name="api",
        identity_role="acme-api",
    )
    assert "astrolift.io/identity-role" in annotations
    assert annotations["astrolift.io/identity-role"] == "acme-api"
    assert "astrolift.io/token-audience" in annotations
    assert "astrolift.io/token-expiration-seconds" in annotations


def test_audience_from_config() -> None:
    driver = ProjectedSaTokenDriver(
        config=ProjectedSaTokenConfig(
            audience="custom-audience",
        )
    )
    driver.create_identity_role(name="r", permissions=[])
    annotations = driver.bind_service_account(
        cluster="x",
        namespace="ns",
        sa_name="api",
        identity_role="r",
    )
    assert annotations["astrolift.io/token-audience"] == "custom-audience"


def test_attach_policy_to_known_role() -> None:
    driver = ProjectedSaTokenDriver()
    driver.create_identity_role(name="acme-api", permissions=[])
    driver.attach_policy(
        role="acme-api",
        policy="arn:platform:policy/extra",
    )
    # Catalog reflects the attachment
    assert any(p.get("managed_policy") == "arn:platform:policy/extra" for p in driver._roles["acme-api"])


def test_attach_policy_unknown_role() -> None:
    driver = ProjectedSaTokenDriver()
    with pytest.raises(KeyError):
        driver.attach_policy(role="missing", policy="x")


def test_delete_role_clears_catalog() -> None:
    driver = ProjectedSaTokenDriver()
    driver.create_identity_role(name="r", permissions=[])
    driver.delete_identity_role("r")
    assert "r" not in driver._roles


def test_delete_unknown_role() -> None:
    driver = ProjectedSaTokenDriver()
    with pytest.raises(KeyError):
        driver.delete_identity_role("never-existed")
