"""Signing keys for brain read tokens (#2320).

ES256 key pairs. The private key is sealed at rest like every other platform
secret; the public half is published as a JWK the cluster edge verifies
tokens against. A key signs for 30 days, then is retired and stays published
for a day so tokens it signed (15 minutes at most) still verify.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class BrainTokenKey(BaseCoreModel):
    kid = models.CharField(max_length=64, unique=True)
    private_key_backend_kind = models.CharField(max_length=32, default="local_fernet")
    private_key_ciphertext = models.BinaryField()
    public_jwk = models.JSONField()
    retired_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"BrainTokenKey({self.kid})"
