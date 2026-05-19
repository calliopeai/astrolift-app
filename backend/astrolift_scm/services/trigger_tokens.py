"""
SourceTriggerToken issue + rotate helpers (#734).

Two primitives:

* ``issue_trigger_token(connection, repo_full_name, kind, plaintext)`` —
  create a fresh token row, encrypting the caller-supplied plaintext.
  Caller obtains the plaintext from the SCM host (GitLab → create
  pipeline-trigger via the UI or REST; Bitbucket → generate API key).
  Returns the new :class:`~astrolift_scm.models.SourceTriggerToken` row.

* ``rotate_trigger_token(token, new_plaintext)`` — atomically soft-deletes
  the existing row and creates a replacement with the new encrypted
  credential. Returns the new row. A grace-window here would require
  the dispatcher to try both old and new on failure; since the host
  invalidates the old token when the operator creates a new one, we
  prefer a clean atomic swap instead.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from astrolift_scm.models import SourceConnection, SourceTriggerToken
from core.secrets import EncryptedSecret, encrypt_at_rest


def issue_trigger_token(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    kind: str,
    plaintext: str,
    expires_at=None,
) -> SourceTriggerToken:
    """Encrypt ``plaintext`` and persist a new SourceTriggerToken row.

    Does not soft-delete any existing active row — the caller should call
    ``rotate_trigger_token`` when replacing an existing credential.
    """
    encrypted: EncryptedSecret = encrypt_at_rest(plaintext.encode("utf-8"))
    return SourceTriggerToken.objects.create(
        source_connection=connection,
        repo_full_name=repo_full_name,
        kind=kind,
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        expires_at=expires_at,
    )


@transaction.atomic
def rotate_trigger_token(
    token: SourceTriggerToken,
    new_plaintext: str,
    *,
    expires_at=None,
) -> SourceTriggerToken:
    """Replace ``token`` with a new row carrying ``new_plaintext``.

    Soft-deletes the old row so the audit trail is preserved (the
    dispatcher always queries ``deleted_at__isnull=True`` so it will
    immediately pick up the new row). Returns the new
    :class:`SourceTriggerToken`.

    ``expires_at`` controls the new token's expiry; if omitted the
    existing value is carried forward.
    """
    now = timezone.now()
    token.deleted_at = now
    token.save(update_fields=["deleted_at", "updated_at", "version"])

    effective_expires_at = expires_at if expires_at is not None else token.expires_at
    encrypted: EncryptedSecret = encrypt_at_rest(new_plaintext.encode("utf-8"))
    return SourceTriggerToken.objects.create(
        source_connection=token.source_connection,
        repo_full_name=token.repo_full_name,
        kind=token.kind,
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        expires_at=effective_expires_at,
    )


def get_active_trigger_token(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    kind: str,
) -> SourceTriggerToken | None:
    """Return the newest active trigger token for the given (connection,
    repo, kind) triple, or None if none exists."""
    return (
        SourceTriggerToken.objects.filter(
            source_connection=connection,
            repo_full_name=repo_full_name,
            kind=kind,
            deleted_at__isnull=True,
        )
        .order_by("-created_at")
        .first()
    )
