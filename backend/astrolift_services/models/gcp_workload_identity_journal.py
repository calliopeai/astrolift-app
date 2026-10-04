"""Private committed ownership and submission metadata for GCP IAM effects."""

from django.db import models

from core.models.base import BaseCoreModel


class GCPWorkloadIdentityJournal(BaseCoreModel):
    class State(models.TextChoices):
        RESERVED = "RESERVED"
        UNSENT = "UNSENT"
        SENT = "SENT"
        UNKNOWN = "UNKNOWN"
        OBSERVED = "OBSERVED"

    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.PROTECT)
    registered_app = models.ForeignKey("astrolift_registry.RegisteredApp", on_delete=models.PROTECT)
    tenant_cluster = models.ForeignKey("astrolift_clusters.TenantCluster", on_delete=models.PROTECT)
    provider_plugin = models.ForeignKey("astrolift_clusters.ProviderPlugin", on_delete=models.PROTECT)
    target_snapshot = models.JSONField()
    target_sha256 = models.CharField(max_length=64)
    context_sha256 = models.CharField(max_length=64)
    ledger = models.JSONField()
    ledger_sha256 = models.CharField(max_length=64)
    operation_id = models.UUIDField()
    reservation_nonce = models.UUIDField()
    generation = models.PositiveBigIntegerField(default=1, db_default=1)
    desired_revision = models.PositiveBigIntegerField()
    observed_revision = models.PositiveBigIntegerField(default=0, db_default=0)
    desired_union_sha256 = models.CharField(max_length=64)
    authority_reference_sha256 = models.CharField(max_length=64)
    workflow_id = models.CharField(max_length=200)
    execution_id = models.CharField(max_length=200)
    state = models.CharField(
        max_length=16, choices=State.choices, default=State.RESERVED, db_default=State.RESERVED
    )
    submissions = models.JSONField(default=dict, db_default={})
    observed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["registered_app", "tenant_cluster"],
                condition=models.Q(deleted_at__isnull=True),
                name="gcp_identity_journal_live_owner",
            ),
            models.CheckConstraint(
                condition=models.Q(generation__gte=1), name="gcp_identity_journal_generation_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(desired_revision__gte=1), name="gcp_identity_journal_revision_positive"
            ),
        ]

    def soft_delete(self, user=None):
        raise ValueError("GCP_IDENTITY_JOURNAL_RETIREMENT_REQUIRES_REVIEW")

    def restore(self):
        raise ValueError("GCP_IDENTITY_JOURNAL_RESTORE_REQUIRES_REVIEW")
