"""Tests for connection secret path + auth modes (#25, spec 11 §10-§14)."""

from __future__ import annotations

import pytest

from astrolift_drivers.connection_secret import (
    AuthMode,
    AuthModeError,
    ConnectionMaterial,
    SecretPathError,
    secret_storage_path,
    validate_material,
    variant_supports_auth_mode,
)

# ---- storage path --------------------------------------------------


def test_path_format():
    out = secret_storage_path(
        env_slug="prod", org_slug="acme", app_slug="api",
        service_name="main-db",
    )
    assert out == "astrolift/prod/acme/api/managed-services/main-db"


def test_path_rejects_empty_segment():
    with pytest.raises(SecretPathError, match="env_slug"):
        secret_storage_path(
            env_slug="", org_slug="acme", app_slug="api",
            service_name="main-db",
        )


def test_path_rejects_slash_in_segment():
    """Smuggled '/' would escape the org's namespace in the backend."""
    with pytest.raises(SecretPathError, match="service_name"):
        secret_storage_path(
            env_slug="prod", org_slug="acme", app_slug="api",
            service_name="../../etc/passwd",
        )


def test_path_rejects_padded_whitespace():
    with pytest.raises(SecretPathError):
        secret_storage_path(
            env_slug=" prod ", org_slug="acme", app_slug="api",
            service_name="db",
        )


# ---- material validation -------------------------------------------


def test_password_mode_clean():
    mat = ConnectionMaterial(
        auth_mode=AuthMode.PASSWORD,
        env_values={"POSTGRES_USER": "u", "POSTGRES_PASSWORD": "p"},
    )
    validate_material(mat)  # no raise


def test_password_mode_rejects_iam_grants():
    mat = ConnectionMaterial(
        auth_mode=AuthMode.PASSWORD,
        env_values={"POSTGRES_PASSWORD": "p"},
        iam_grants=({"action": "rds:Connect"},),
    )
    with pytest.raises(AuthModeError, match="iam_grants"):
        validate_material(mat)


def test_password_mode_rejects_mtls_files():
    mat = ConnectionMaterial(
        auth_mode=AuthMode.PASSWORD,
        env_values={"POSTGRES_PASSWORD": "p"},
        mtls_files={"client.crt": b"---"},
    )
    with pytest.raises(AuthModeError, match="mtls_files"):
        validate_material(mat)


def test_iam_mode_clean():
    mat = ConnectionMaterial(
        auth_mode=AuthMode.IAM,
        env_values={
            "POSTGRES_HOST": "db.acme.svc",
            "POSTGRES_DB": "main",
        },
        iam_grants=(
            {"action": "rds-db:connect", "resource": "arn:..."},
        ),
    )
    validate_material(mat)


def test_iam_mode_rejects_password_in_envs():
    """Workload identity makes credentials unnecessary; leaking a
    password key into IAM mode is a misconfigured driver."""
    mat = ConnectionMaterial(
        auth_mode=AuthMode.IAM,
        env_values={
            "POSTGRES_HOST": "x",
            "POSTGRES_PASSWORD": "should-not-be-here",
        },
    )
    with pytest.raises(AuthModeError, match="password keys"):
        validate_material(mat)


def test_iam_mode_rejects_mtls_files():
    mat = ConnectionMaterial(
        auth_mode=AuthMode.IAM,
        env_values={"POSTGRES_HOST": "x"},
        mtls_files={"x": b"y"},
    )
    with pytest.raises(AuthModeError, match="mtls_files"):
        validate_material(mat)


def test_mtls_mode_clean():
    mat = ConnectionMaterial(
        auth_mode=AuthMode.MTLS,
        env_values={"POSTGRES_HOST": "x", "POSTGRES_TLS_CERT_PATH": "/var/run/mtls/cert"},
        mtls_files={"client.crt": b"---", "client.key": b"---"},
    )
    validate_material(mat)


def test_mtls_mode_requires_files():
    """No certs → can't actually authenticate. Surface the bug."""
    mat = ConnectionMaterial(
        auth_mode=AuthMode.MTLS,
        env_values={"POSTGRES_HOST": "x"},
        mtls_files={},
    )
    with pytest.raises(AuthModeError, match="no mtls_files"):
        validate_material(mat)


# ---- variant support -----------------------------------------------


def test_variant_supports_check():
    """Variant declares which modes it accepts; manifest+org pick
    one. Caller checks before validating the material."""
    aurora = frozenset({AuthMode.PASSWORD, AuthMode.IAM})
    assert variant_supports_auth_mode(allowed_modes=aurora, requested=AuthMode.IAM) is True
    assert variant_supports_auth_mode(allowed_modes=aurora, requested=AuthMode.MTLS) is False


def test_variant_supports_empty_set():
    """A variant with no auth_mode declared is a misconfig — no
    requested mode should pass."""
    assert variant_supports_auth_mode(allowed_modes=frozenset(), requested=AuthMode.PASSWORD) is False
