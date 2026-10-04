"""Private append-only human authority provenance, never deployment configuration."""

from django.db import models

from core.models.base import BaseCoreModel


class DeploymentIdentityOrigin(BaseCoreModel):
    deployment = models.OneToOneField(
        "astrolift_lifecycle.Deployment", on_delete=models.PROTECT, related_name="identity_origin"
    )
    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.PROTECT)
    authority_reference = models.JSONField()
    authority_sha256 = models.CharField(max_length=64)
    deployment_binding = models.CharField(max_length=64)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("DEPLOYMENT_ORIGIN_IMMUTABLE")
        return super().save(*args, **kwargs)

    def soft_delete(self, **kwargs):
        raise ValueError("DEPLOYMENT_ORIGIN_IMMUTABLE")

    def restore(self):
        raise ValueError("DEPLOYMENT_ORIGIN_IMMUTABLE")
