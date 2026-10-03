"""Local model sources are independent from application and pipeline lifecycles."""

from django.db import models

from core.models.base import BaseCoreModel


class LocalModelArtifact(BaseCoreModel):
    class State(models.TextChoices):
        UPLOADING = "uploading"
        VERIFIED = "verified"

    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.PROTECT)
    name = models.CharField(max_length=128)
    state = models.CharField(max_length=16, choices=State.choices, default=State.UPLOADING)
    manifest_sha256 = models.CharField(max_length=64)
    manifest = models.JSONField(default=list)
    storage_source = models.CharField(max_length=64)
    storage_receipt = models.JSONField(default=dict, editable=False)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["organization", "state"], name="local_model_org_state")]
