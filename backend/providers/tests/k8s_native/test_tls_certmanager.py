"""Tests for cert-manager TlsDriver (#50)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from _sdk.cluster import ApplyResult, DeleteResult
from k8s_native.tls_certmanager import (
    CertManagerConfig,
    CertManagerDriver,
)


def test_render_certificate_basic() -> None:
    driver = CertManagerDriver()
    cert = driver.render_certificate(domain="api.acme.example")
    assert cert["kind"] == "Certificate"
    assert cert["spec"]["commonName"] == "api.acme.example"
    assert "api.acme.example" in cert["spec"]["dnsNames"]


def test_render_uses_cluster_issuer() -> None:
    driver = CertManagerDriver(
        config=CertManagerConfig(
            cluster_issuer="letsencrypt-staging",
        )
    )
    cert = driver.render_certificate(domain="api.acme.example")
    assert cert["spec"]["issuerRef"]["name"] == "letsencrypt-staging"
    assert cert["spec"]["issuerRef"]["kind"] == "ClusterIssuer"


def test_render_certificate_with_sans() -> None:
    driver = CertManagerDriver()
    cert = driver.render_certificate(
        domain="api.acme.example",
        sans=["www.acme.example"],
    )
    assert "api.acme.example" in cert["spec"]["dnsNames"]
    assert "www.acme.example" in cert["spec"]["dnsNames"]


def test_unknown_strategy_rejected() -> None:
    driver = CertManagerDriver()
    with pytest.raises(ValueError):
        driver.ensure_certificate(
            domain="api.acme.example",
            strategy="weird",
        )


def test_ensure_certificate_returns_pending() -> None:
    """cert-manager populates not_before/not_after async."""
    driver = CertManagerDriver()
    result = driver.ensure_certificate(domain="api.acme.example")
    assert result.status == "pending"
    assert result.not_before is None
    assert result.not_after is None


def test_ensure_certificate_applies_when_cluster_driver_bound() -> None:
    cluster_driver = MagicMock()
    cluster_driver.apply_manifests.return_value = ApplyResult(
        created=["Certificate/api-acme-example"],
        updated=[],
        unchanged=[],
        errors=[],
    )
    driver = CertManagerDriver(
        config=CertManagerConfig(
            cluster_driver=cluster_driver,
        )
    )
    driver.ensure_certificate(domain="api.acme.example")
    cluster_driver.apply_manifests.assert_called_once()


def test_revoke_certificate_calls_delete() -> None:
    cluster_driver = MagicMock()
    cluster_driver.delete_manifests.return_value = DeleteResult(
        deleted=["Certificate/api-acme-example"],
        not_found=[],
        errors=[],
    )
    driver = CertManagerDriver(
        config=CertManagerConfig(
            cluster_driver=cluster_driver,
        )
    )
    driver.revoke_certificate("api-acme-example")
    cluster_driver.delete_manifests.assert_called_once()


def test_get_certificate_unknown_status_without_cluster_driver() -> None:
    driver = CertManagerDriver()
    cert = driver.get_certificate("some-cert-id")
    assert cert.status == "unknown"
