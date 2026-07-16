"""Tests for AWS SecretsBackend (#32)."""

from __future__ import annotations

import pytest

from aws._errors import NotFoundError
from aws.secrets import AWSSecretsBackend, SecretsConfig, _split_backend

# ---- routing -----------------------------------------------------


@pytest.mark.parametrize(
    "path,expected_backend,expected_sub",
    [
        ("sm:my-app/db", "sm", "my-app/db"),
        ("ssm:my-app/db", "ssm", "my-app/db"),
        ("my-app/db", "sm", "my-app/db"),  # default
        ("ssm:/my/path", "ssm", "/my/path"),
    ],
)
def test_split_backend(path, expected_backend, expected_sub):
    backend, sub = _split_backend(path)
    assert backend == expected_backend
    assert sub == expected_sub


# ---- Secrets Manager backend -------------------------------------


@pytest.fixture
def sm_backend(secrets_client, ssm_client) -> AWSSecretsBackend:
    return AWSSecretsBackend(
        config=SecretsConfig(region="us-east-1"),
        sm_client=secrets_client,
        ssm_client=ssm_client,
    )


def test_sm_upsert_then_get_round_trip(sm_backend: AWSSecretsBackend) -> None:
    sm_backend.upsert("my-app/db", {"DATABASE_URL": "postgres://x"})
    got = sm_backend.get("my-app/db")
    assert got == {"DATABASE_URL": "postgres://x"}


def test_sm_get_missing_returns_none(sm_backend: AWSSecretsBackend) -> None:
    """Get-missing is None, NOT an exception. Workflow uses None
    to detect first-time bind."""
    assert sm_backend.get("missing/path") is None


def test_sm_upsert_overwrites(sm_backend: AWSSecretsBackend) -> None:
    sm_backend.upsert("my-app/db", {"v1": "old"})
    sm_backend.upsert("my-app/db", {"v2": "new"})
    assert sm_backend.get("my-app/db") == {"v2": "new"}


def test_sm_delete_uses_recovery_window(sm_backend: AWSSecretsBackend, secrets_client) -> None:
    """Spec invariant: don't hard-delete; 7-day recovery window."""
    sm_backend.upsert("my-app/db", {"K": "v"})
    sm_backend.delete("my-app/db")
    # moto: secret should be marked deleted with a recovery window
    response = secrets_client.list_secrets(IncludePlannedDeletion=True)
    deleted = [s for s in response["SecretList"] if "DeletedDate" in s]
    assert len(deleted) > 0


def test_sm_delete_missing_raises_not_found(sm_backend: AWSSecretsBackend) -> None:
    with pytest.raises(NotFoundError):
        sm_backend.delete("never/existed")


def test_sm_list_prefix(sm_backend: AWSSecretsBackend) -> None:
    sm_backend.upsert("acme/api", {"K": "v"})
    sm_backend.upsert("acme/worker", {"K": "v"})
    sm_backend.upsert("globex/api", {"K": "v"})

    result = sm_backend.list("acme")
    assert sorted(result) == ["acme/api", "acme/worker"]


def test_sm_handles_plain_string_secret(
    sm_backend: AWSSecretsBackend,
    secrets_client,
) -> None:
    """Externally-created secrets might be plain strings, not JSON.
    Wrap under 'value' key to match contract."""
    secrets_client.create_secret(
        Name="astrolift/legacy/path",
        SecretString="plain-token",
    )
    got = sm_backend.get("legacy/path")
    assert got == {"value": "plain-token"}


# ---- SSM backend -------------------------------------------------


@pytest.fixture
def ssm_backend(secrets_client, ssm_client) -> AWSSecretsBackend:
    return AWSSecretsBackend(
        config=SecretsConfig(region="us-east-1"),
        sm_client=secrets_client,
        ssm_client=ssm_client,
    )


def test_ssm_upsert_then_get_round_trip(ssm_backend: AWSSecretsBackend) -> None:
    ssm_backend.upsert("ssm:my-app/cfg", {"FEATURE": "on"})
    got = ssm_backend.get("ssm:my-app/cfg")
    assert got == {"FEATURE": "on"}


def test_ssm_uses_securestring(ssm_backend: AWSSecretsBackend, ssm_client) -> None:
    """SSM-backed values are SecureString (KMS-encrypted at rest)."""
    ssm_backend.upsert("ssm:my-app/secret", {"TOKEN": "x"})
    response = ssm_client.describe_parameters(
        ParameterFilters=[
            {
                "Key": "Name",
                "Option": "BeginsWith",
                "Values": ["/astrolift/my-app/"],
            }
        ],
    )
    assert response["Parameters"][0]["Type"] == "SecureString"


def test_ssm_get_missing_returns_none(ssm_backend: AWSSecretsBackend) -> None:
    assert ssm_backend.get("ssm:missing/path") is None


def test_ssm_delete_missing_raises_not_found(
    ssm_backend: AWSSecretsBackend,
) -> None:
    with pytest.raises(NotFoundError):
        ssm_backend.delete("ssm:never/existed")


def test_ssm_list_prefix(ssm_backend: AWSSecretsBackend) -> None:
    ssm_backend.upsert("ssm:acme/api", {"K": "v"})
    ssm_backend.upsert("ssm:acme/worker", {"K": "v"})
    ssm_backend.upsert("ssm:globex/api", {"K": "v"})
    result = ssm_backend.list("ssm:acme")
    assert sorted(result) == ["acme/api", "acme/worker"]


# ---- backend-routing E2E -----------------------------------------


def test_sm_and_ssm_dont_collide(
    sm_backend: AWSSecretsBackend,
) -> None:
    """Same logical path on different backends keeps state isolated."""
    sm_backend.upsert("my-app/v", {"in": "sm"})
    sm_backend.upsert("ssm:my-app/v", {"in": "ssm"})

    assert sm_backend.get("my-app/v") == {"in": "sm"}
    assert sm_backend.get("ssm:my-app/v") == {"in": "ssm"}


def test_sm_name_no_double_prefix():
    """Absolute refs that already carry the backend prefix (e.g. a managed-
    service driver stored at 'astrolift/rds/<inst>/url' and the binding
    references it verbatim) must NOT be re-prefixed to
    'astrolift/astrolift/...' — that 404s and breaks binding resolution."""
    from unittest.mock import MagicMock

    from aws.secrets import AWSSecretsBackend, SecretsConfig

    b = AWSSecretsBackend(
        config=SecretsConfig(region="us-east-1"),  # prefix defaults to "astrolift"
        sm_client=MagicMock(),
        ssm_client=MagicMock(),
    )
    # relative ref → prefixed
    assert b._sm_name("acme/app/prod/db") == "astrolift/acme/app/prod/db"
    # absolute ref already carrying the prefix → used verbatim
    assert b._sm_name("astrolift/rds/inst/url") == "astrolift/rds/inst/url"
    assert b._sm_name("/astrolift/rds/inst/url") == "astrolift/rds/inst/url"
    assert b._sm_name("astrolift") == "astrolift"
