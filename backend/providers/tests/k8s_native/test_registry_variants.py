"""Tests for the named OCI registry variants (#60)."""

from __future__ import annotations

import base64
import json

import pytest

from k8s_native.registry_oci import OCIRegistryDriver
from k8s_native.registry_variants import (
    dockerhub_driver,
    ghcr_driver,
    harbor_driver,
    quay_driver,
)


def test_quay_driver_targets_quay_io() -> None:
    driver = quay_driver(
        organization="acme",
        username="robot+publish",
        password="r3dacted",
    )
    repo = driver.ensure_repo("api")
    assert repo.uri == "quay.io/acme/api"


def test_dockerhub_driver_targets_docker_io() -> None:
    driver = dockerhub_driver(
        namespace="acme",
        username="acme",
        password="r3dacted",
    )
    repo = driver.ensure_repo("api")
    assert repo.uri == "docker.io/acme/api"


def test_ghcr_driver_uses_pat_as_password() -> None:
    driver = ghcr_driver(
        organization="acme",
        pat="ghp_xyz",
    )
    secret = driver.get_pull_secret(
        cluster="prod", namespace="acme-api",
    )
    docker = json.loads(
        base64.b64decode(secret["data"][".dockerconfigjson"]).decode(),
    )
    # ghcr.io is the auth host (organization is path-level)
    assert "ghcr.io/acme" in docker["auths"]


def test_harbor_driver_includes_project_path() -> None:
    driver = harbor_driver(
        base_url="https://harbor.example.com",
        project="astrolift",
        robot_username="robot$astrolift+push",
        robot_password="r3dacted",
    )
    repo = driver.ensure_repo("api")
    assert repo.uri == "harbor.example.com/astrolift/api"


def test_quay_driver_returns_real_oci_driver() -> None:
    """The factory returns a working driver, not a stub."""
    driver = quay_driver(
        organization="acme",
        username="u", password="p",
    )
    assert isinstance(driver, OCIRegistryDriver)


def test_pull_secret_credentials_round_trip() -> None:
    driver = dockerhub_driver(
        namespace="acme", username="user", password="pass",
    )
    secret = driver.get_pull_secret(
        cluster="prod", namespace="acme-api",
    )
    docker = json.loads(
        base64.b64decode(secret["data"][".dockerconfigjson"]).decode(),
    )
    auth = docker["auths"]["docker.io/acme"]["auth"]
    decoded = base64.b64decode(auth).decode()
    assert decoded == "user:pass"


def test_quay_driver_without_credentials_renders_uri() -> None:
    """Public Quay images don't need credentials — driver still
    builds + ensure_repo works."""
    driver = quay_driver(organization="public")
    repo = driver.ensure_repo("api")
    assert repo.uri == "quay.io/public/api"
    # But pull-secret rendering should fail clearly
    with pytest.raises(RuntimeError, match="credentials"):
        driver.get_pull_secret(cluster="c", namespace="n")
