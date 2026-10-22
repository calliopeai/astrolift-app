"""Append-only operator settlement of a native journal whose send was never resolved (#2278)."""

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class GCPNativeJournalResolution(BaseCoreModel):
    """Who settled which stuck operation, what was unresolved, and on what attestation.

    A settlement never claims the send was observed. It records the dropped
    intents so the operator can check the cluster and the Endpoint policies.
    """

    class Kind(models.TextChoices):
        PREPARATION = "PREPARATION"
        IAM = "IAM"
        APPLY = "APPLY"

    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.PROTECT)
    registered_app = models.ForeignKey("astrolift_registry.RegisteredApp", on_delete=models.PROTECT)
    tenant_cluster = models.ForeignKey("astrolift_clusters.TenantCluster", on_delete=models.PROTECT)
    # Empty for a teardown's grant removal (#2278), which no Deployment owns.
    deployment = models.ForeignKey(
        "astrolift_lifecycle.Deployment", null=True, blank=True, on_delete=models.PROTECT
    )
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    journal_guid = models.UUIDField()
    operation_id = models.UUIDField()
    generation = models.PositiveBigIntegerField()
    prior_state = models.CharField(max_length=16)
    prior_ledger_sha256 = models.CharField(max_length=64)
    resolved_ledger_sha256 = models.CharField(max_length=64)
    unresolved = models.JSONField()
    attestation = models.CharField(max_length=500)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "journal_guid", "operation_id"], name="gcp_native_resolution_once"
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("GCP_NATIVE_RESOLUTION_IMMUTABLE")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("GCP_NATIVE_RESOLUTION_IMMUTABLE")

    def soft_delete(self, **kwargs):
        raise ValueError("GCP_NATIVE_RESOLUTION_IMMUTABLE")

    def restore(self):
        raise ValueError("GCP_NATIVE_RESOLUTION_IMMUTABLE")
