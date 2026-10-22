"""Signing keys for brain read tokens (#2320).

ES256 key pairs. The private key is sealed at rest like every other platform
secret; the public half is published as a JWK the cluster edge verifies
tokens against. A key signs for 30 days, then is retired and stays published
for a day so tokens it signed (15 minutes at most) still verify.

A key with a ``binding`` signs only the tokens of one app hosted on a brain
(#2243), one per deploy of an environment; it is published while its binding,
app and brain are live, which is how deleting any of them revokes it.

A key with a ``run_binding`` signs only the tokens one agent run reads one
brain with (#2432); it is published while the run is in flight and the
binding and brain are live.

``typed_contract`` marks a hosted-app deployment key that signed a typed
token (#2470): while one is published, the brain's consumer stays staged.
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
    binding = models.ForeignKey(
        "astrolift_registry.BrainAppBinding",
        related_name="keys",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    deployment = models.ForeignKey(
        "astrolift_lifecycle.Deployment",
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    run_binding = models.ForeignKey(
        "astrolift_registry.BrainRunBinding",
        related_name="keys",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    typed_contract = models.CharField(max_length=8, blank=True, default="", db_default="")

    def __str__(self) -> str:
        return f"BrainTokenKey({self.kid})"
