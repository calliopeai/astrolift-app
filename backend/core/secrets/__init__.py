"""
Pluggable secret-at-rest layer.

The platform stores credentials (SCM tokens, SSH private keys,
OAuth client secrets) at rest. Where those bytes actually live
depends on the install:

- ``local_fernet`` (default): bytes are encrypted with a Fernet key
  derived from ``settings.SECRET_KEY`` and stored in-row. Suitable
  for OSS / dev / single-tenant installs without a cloud KMS.
- ``aws_secrets_manager`` / ``gcp_secret_manager`` /
  ``azure_key_vault`` (later): the ciphertext column carries an
  opaque reference (ARN, resource id) and the cloud backend fetches
  the actual value on read.

Every row that stores an encrypted secret carries a small tag
(``backend_kind`` + ``backend_ref``) so the install can migrate
between backends without rewriting model code — a one-shot
``manage.py migrate_secrets --to=aws_secrets_manager`` command
re-encrypts in place.

Use ``encrypt_at_rest()`` / ``decrypt()`` for write/read; both go
through the active backend selected by
``settings.ASTROLIFT_SECRETS_BACKEND`` (default ``local_fernet``).
"""

from __future__ import annotations

import dataclasses

from .local_fernet import LocalFernetBackend


@dataclasses.dataclass(frozen=True, slots=True)
class EncryptedSecret:
    """The shape stored on every row that holds a credential.

    ``backend_kind`` lets the migration command know which backend
    produced the ciphertext; ``backend_ref`` is the opaque value
    (raw ciphertext bytes for local_fernet, an ARN/resource-id for
    cloud KMS backends).
    """

    backend_kind: str
    backend_ref: bytes


_BACKENDS = {
    "local_fernet": LocalFernetBackend,
}


def secrets_backend():
    """Return the configured secrets backend instance.

    Lazy-initialized so settings are guaranteed to be loaded.
    """
    from django.conf import settings

    kind = getattr(settings, "ASTROLIFT_SECRETS_BACKEND", "local_fernet")
    cls = _BACKENDS.get(kind)
    if cls is None:
        raise RuntimeError(
            f"unknown ASTROLIFT_SECRETS_BACKEND={kind!r}; "
            f"valid options: {sorted(_BACKENDS)}"
        )
    return cls()


def encrypt_at_rest(plaintext: bytes) -> EncryptedSecret:
    backend = secrets_backend()
    return EncryptedSecret(
        backend_kind=backend.kind, backend_ref=backend.encrypt(plaintext)
    )


def decrypt(secret: EncryptedSecret) -> bytes:
    """Decrypt a stored secret using the backend that produced it.

    Cross-backend reads are supported (a row encrypted with
    local_fernet still decrypts under a future aws_secrets_manager
    install) — every backend is registered, the row's
    ``backend_kind`` selects which one to call.
    """
    cls = _BACKENDS.get(secret.backend_kind)
    if cls is None:
        raise RuntimeError(
            f"row was encrypted with unknown backend "
            f"{secret.backend_kind!r}; cannot decrypt"
        )
    return cls().decrypt(secret.backend_ref)


__all__ = [
    "EncryptedSecret",
    "decrypt",
    "encrypt_at_rest",
    "secrets_backend",
]
