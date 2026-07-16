"""Tests for generic OCI ImageRegistryDriver (#52)."""

from __future__ import annotations

import base64
import json

import pytest

from k8s_native.registry_oci import OCIRegistryConfig, OCIRegistryDriver


def test_ensure_repo_returns_uri() -> None:
    driver = OCIRegistryDriver(
        config=OCIRegistryConfig(
            registry_url="https://harbor.example",
        )
    )
    repo = driver.ensure_repo("acme/api")
    assert repo.name == "acme/api"
    assert "harbor.example" in repo.uri
    assert "acme/api" in repo.uri


def test_push_returns_canonical_ref() -> None:
    driver = OCIRegistryDriver(
        config=OCIRegistryConfig(
            registry_url="https://ghcr.io",
        )
    )
    ref = driver.push(local_image="my:dev", repo="acme/api", tag="v1")
    assert ref == "ghcr.io/acme/api:v1"


def test_push_rejects_empty_local_image() -> None:
    driver = OCIRegistryDriver(
        config=OCIRegistryConfig(
            registry_url="https://harbor.example",
        )
    )
    with pytest.raises(ValueError):
        driver.push(local_image="", repo="r", tag="t")


def test_pull_secret_shape() -> None:
    driver = OCIRegistryDriver(
        config=OCIRegistryConfig(
            registry_url="https://harbor.example",
            username="robot$astrolift",
            password="topsecret",
        )
    )
    secret = driver.get_pull_secret(cluster="c", namespace="ns")
    assert secret["type"] == "kubernetes.io/dockerconfigjson"
    assert secret["metadata"]["name"] == "astrolift-oci-credentials"
    encoded = secret["data"][".dockerconfigjson"]
    decoded = json.loads(base64.b64decode(encoded).decode())
    assert "harbor.example" in decoded["auths"]
    auth_field = decoded["auths"]["harbor.example"]["auth"]
    assert base64.b64decode(auth_field).decode() == ("robot$astrolift:topsecret")


def test_pull_secret_requires_creds() -> None:
    driver = OCIRegistryDriver(
        config=OCIRegistryConfig(
            registry_url="https://harbor.example",
        )
    )
    with pytest.raises(RuntimeError, match="credentials"):
        driver.get_pull_secret(cluster="c", namespace="ns")


def test_delete_repo_archive_is_noop() -> None:
    driver = OCIRegistryDriver(
        config=OCIRegistryConfig(
            registry_url="https://harbor.example",
        )
    )
    driver.delete_repo("acme/api", archive=True)


def test_delete_repo_force_not_implemented() -> None:
    driver = OCIRegistryDriver(
        config=OCIRegistryConfig(
            registry_url="https://harbor.example",
        )
    )
    with pytest.raises(NotImplementedError):
        driver.delete_repo("acme/api", archive=False)


def test_list_tags_with_no_http_client_returns_empty() -> None:
    driver = OCIRegistryDriver(
        config=OCIRegistryConfig(
            registry_url="https://harbor.example",
        )
    )
    assert driver.list_tags("acme/api") == []
