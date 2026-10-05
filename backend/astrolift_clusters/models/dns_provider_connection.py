"""Organization-owned read-only DNS credentials and single-use OAuth attempts."""

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class DnsProviderConnection(BaseCoreModel):
    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.PROTECT)
    name = models.CharField(max_length=128)
    provider = models.CharField(max_length=32, default="CLOUDFLARE", editable=False)
    auth_method = models.CharField(max_length=16)
    oauth_client_id = models.CharField(max_length=128, blank=True)
    state = models.CharField(max_length=32, default="ACTIVE")
    revocation_state = models.CharField(max_length=32, default="NOT_REQUESTED")
    secret_backend_kind = models.CharField(max_length=32)
    secret_ciphertext = models.BinaryField()
    refresh_backend_kind = models.CharField(max_length=32, blank=True)
    refresh_ciphertext = models.BinaryField(default=bytes)
    verified_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField(null=True)

    def save(self, *args, **kwargs):
        if self.pk:
            original = (
                type(self)
                .all_objects.filter(pk=self.pk)
                .values("organization_id", "provider", "auth_method", "oauth_client_id")
                .get()
            )
            if any(getattr(self, key) != value for key, value in original.items()):
                raise ValueError("DNS connection ownership cannot change.")
        super().save(*args, **kwargs)


class DnsProviderOAuthAttempt(BaseCoreModel):
    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    name = models.CharField(max_length=128)
    state_sha256 = models.CharField(max_length=64, unique=True)
    session_hmac = models.CharField(max_length=64)
    client_sha256 = models.CharField(max_length=64)
    verifier_backend_kind = models.CharField(max_length=32)
    verifier_ciphertext = models.BinaryField()
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True)
