"""
Default local-only secret backend.

Encrypts plaintext bytes with a Fernet key derived from
``settings.SECRET_KEY`` (HKDF + base64-urlsafe). The ciphertext is
stored in-row on the consuming model.

Why HKDF over the SECRET_KEY rather than reusing it directly:
Fernet wants exactly 32 bytes of key material; SECRET_KEY is a
random string of arbitrary length. HKDF-SHA256 with a fixed info
label produces a stable 32-byte key from any SECRET_KEY value, so
operators can rotate SECRET_KEY independently of every other Django
crypto path that consumes it (CSRF, session signing, etc.). The
``info`` is namespaced so a future need for a separate
data-encryption key derives differently and doesn't collide.
"""

from __future__ import annotations

import base64

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class LocalFernetBackend:
    kind = "local_fernet"

    def _key(self) -> bytes:
        from django.conf import settings

        material = settings.SECRET_KEY.encode("utf-8")
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"astrolift.secrets.local_fernet.v1",
            info=b"astrolift-secret-at-rest",
        ).derive(material)
        return base64.urlsafe_b64encode(derived)

    def encrypt(self, plaintext: bytes) -> bytes:
        return Fernet(self._key()).encrypt(plaintext)

    def decrypt(self, ciphertext: bytes) -> bytes:
        return Fernet(self._key()).decrypt(ciphertext)
