"""Reusable organization-owned Hugging Face credentials; values are never projected."""

from django.db import models

from core.models.base import BaseCoreModel


class HuggingFaceConnection(BaseCoreModel):
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        on_delete=models.PROTECT,
        related_name="hugging_face_connections",
    )
    name = models.CharField(max_length=128)
    account_username = models.CharField(max_length=96)
    secret_backend_kind = models.CharField(max_length=32)
    secret_ciphertext = models.BinaryField()
    verified_at = models.DateTimeField()
