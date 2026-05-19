"""Tests for SourceTriggerToken issue + rotate (#734)."""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization
from astrolift_scm.models import SourceConnection, SourceTriggerToken
from astrolift_scm.services.trigger_tokens import (
    get_active_trigger_token,
    issue_trigger_token,
    rotate_trigger_token,
)
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme SCM", slug="acme-scm-trigger-test")


@pytest.fixture
def connection(org):
    encrypted = encrypt_at_rest(b"glat-test-token")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITLAB_OAUTH_USER,
        display_name="GitLab: acme",
        account_login="acme",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


# ---------------------------------------------------------------------------
# issue_trigger_token
# ---------------------------------------------------------------------------


def test_issue_stores_encrypted_secret(connection):
    token = issue_trigger_token(
        connection,
        repo_full_name="acme/api",
        kind=SourceTriggerToken.Kind.GITLAB_PIPELINE_TRIGGER,
        plaintext="glptt-abc123",
    )

    assert token.pk is not None
    assert token.deleted_at is None
    assert token.repo_full_name == "acme/api"
    assert token.kind == SourceTriggerToken.Kind.GITLAB_PIPELINE_TRIGGER

    # Verify the stored ciphertext decrypts to the original plaintext.
    recovered = decrypt(
        EncryptedSecret(
            backend_kind=token.secret_backend_kind,
            backend_ref=bytes(token.secret_ciphertext),
        )
    )
    assert recovered == b"glptt-abc123"


def test_issue_sets_expires_at(connection):
    from django.utils import timezone

    expiry = timezone.now().replace(microsecond=0)
    token = issue_trigger_token(
        connection,
        repo_full_name="acme/api",
        kind=SourceTriggerToken.Kind.BITBUCKET_PIPELINE_API_KEY,
        plaintext="bbkey-xyz",
        expires_at=expiry,
    )
    assert token.expires_at == expiry


def test_issue_null_expires_at_by_default(connection):
    token = issue_trigger_token(
        connection,
        repo_full_name="acme/api",
        kind=SourceTriggerToken.Kind.GITEA_WORKFLOW_DISPATCH,
        plaintext="gitea-tok",
    )
    assert token.expires_at is None


# ---------------------------------------------------------------------------
# rotate_trigger_token
# ---------------------------------------------------------------------------


def test_rotate_soft_deletes_old_and_creates_new(connection):
    old = issue_trigger_token(
        connection,
        repo_full_name="acme/api",
        kind=SourceTriggerToken.Kind.GITLAB_PIPELINE_TRIGGER,
        plaintext="glptt-old",
    )
    new = rotate_trigger_token(old, "glptt-new")

    old.refresh_from_db()
    assert old.deleted_at is not None

    assert new.pk != old.pk
    assert new.deleted_at is None
    assert new.source_connection_id == connection.pk
    assert new.repo_full_name == "acme/api"
    assert new.kind == SourceTriggerToken.Kind.GITLAB_PIPELINE_TRIGGER

    recovered = decrypt(
        EncryptedSecret(
            backend_kind=new.secret_backend_kind,
            backend_ref=bytes(new.secret_ciphertext),
        )
    )
    assert recovered == b"glptt-new"


def test_rotate_carries_forward_expires_at(connection):
    from django.utils import timezone

    expiry = timezone.now().replace(microsecond=0)
    old = issue_trigger_token(
        connection,
        repo_full_name="acme/api",
        kind=SourceTriggerToken.Kind.GITLAB_PIPELINE_TRIGGER,
        plaintext="glptt-old",
        expires_at=expiry,
    )
    new = rotate_trigger_token(old, "glptt-new")
    assert new.expires_at == expiry


def test_rotate_overrides_expires_at(connection):
    from django.utils import timezone

    old_expiry = timezone.now().replace(microsecond=0)
    new_expiry = timezone.now().replace(microsecond=0)

    old = issue_trigger_token(
        connection,
        repo_full_name="acme/api",
        kind=SourceTriggerToken.Kind.GITLAB_PIPELINE_TRIGGER,
        plaintext="glptt-old",
        expires_at=old_expiry,
    )
    new = rotate_trigger_token(old, "glptt-new", expires_at=new_expiry)
    assert new.expires_at == new_expiry


# ---------------------------------------------------------------------------
# get_active_trigger_token
# ---------------------------------------------------------------------------


def test_get_active_returns_newest(connection):
    first = issue_trigger_token(
        connection,
        repo_full_name="acme/api",
        kind=SourceTriggerToken.Kind.GITLAB_PIPELINE_TRIGGER,
        plaintext="old",
    )
    second = rotate_trigger_token(first, "new")

    active = get_active_trigger_token(
        connection,
        repo_full_name="acme/api",
        kind=SourceTriggerToken.Kind.GITLAB_PIPELINE_TRIGGER,
    )
    assert active is not None
    assert active.pk == second.pk


def test_get_active_returns_none_when_missing(connection):
    result = get_active_trigger_token(
        connection,
        repo_full_name="acme/nonexistent",
        kind=SourceTriggerToken.Kind.GITLAB_PIPELINE_TRIGGER,
    )
    assert result is None


def test_get_active_ignores_soft_deleted(connection):
    token = issue_trigger_token(
        connection,
        repo_full_name="acme/api",
        kind=SourceTriggerToken.Kind.GITLAB_PIPELINE_TRIGGER,
        plaintext="tok",
    )
    from django.utils import timezone

    token.deleted_at = timezone.now()
    token.save(update_fields=["deleted_at", "updated_at", "version"])

    result = get_active_trigger_token(
        connection,
        repo_full_name="acme/api",
        kind=SourceTriggerToken.Kind.GITLAB_PIPELINE_TRIGGER,
    )
    assert result is None
