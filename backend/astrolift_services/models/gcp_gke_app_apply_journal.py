"""Protected metadata-only app resources and immutable accepted execution history."""

from django.db import models

from core.models.base import BaseCoreModel


class GCPGKEAppApplyJournal(BaseCoreModel):
    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.PROTECT)
    registered_app = models.ForeignKey("astrolift_registry.RegisteredApp", on_delete=models.PROTECT)
    app_environment = models.ForeignKey("astrolift_lifecycle.AppEnvironment", on_delete=models.PROTECT)
    tenant_cluster = models.ForeignKey("astrolift_clusters.TenantCluster", on_delete=models.PROTECT)
    provider_plugin = models.ForeignKey("astrolift_clusters.ProviderPlugin", on_delete=models.PROTECT)
    namespace = models.CharField(max_length=63)
    original_target = models.JSONField()
    original_target_sha256 = models.CharField(max_length=64)
    operation_id = models.UUIDField()
    nonce = models.UUIDField()
    generation = models.PositiveBigIntegerField(default=1, db_default=1)
    state = models.CharField(max_length=16, default="RESERVED", db_default="RESERVED")
    ledger = models.JSONField()
    ledger_sha256 = models.CharField(max_length=64)
    observed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["registered_app", "tenant_cluster", "namespace"], name="gcp_app_apply_physical_owner"
            ),
            models.CheckConstraint(
                condition=models.Q(generation__gte=1), name="gcp_app_apply_generation_positive"
            ),
        ]

    def soft_delete(self, *, by=None):
        raise ValueError("GCP_APP_APPLY_RETIREMENT_REQUIRES_REVIEW")

    def restore(self):
        raise ValueError("GCP_APP_APPLY_RESTORE_REQUIRES_REVIEW")


class GCPGKEAppApplyOperation(BaseCoreModel):
    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.PROTECT)
    journal = models.ForeignKey(GCPGKEAppApplyJournal, on_delete=models.PROTECT, related_name="operations")
    deployment = models.ForeignKey("astrolift_lifecycle.Deployment", on_delete=models.PROTECT)
    execution_receipt = models.ForeignKey(
        "astrolift_lifecycle.DeploymentExecutionReceipt", on_delete=models.PROTECT
    )
    identity_source = models.ForeignKey("astrolift_services.GCPAppIdentitySource", on_delete=models.PROTECT)
    preparation_operation = models.ForeignKey(
        "astrolift_services.GCPGKEPreparationOperation", on_delete=models.PROTECT
    )
    operation_id = models.UUIDField(unique=True)
    nonce = models.UUIDField()
    generation = models.PositiveBigIntegerField()
    workflow_id = models.CharField(max_length=200)
    execution_id = models.CharField(max_length=200)
    accepted_plan = models.JSONField()
    accepted_plan_sha256 = models.CharField(max_length=64)
    source_fence = models.JSONField()
    prepared_identity = models.JSONField()
    state = models.CharField(max_length=16, default="RESERVED", db_default="RESERVED")
    ledger = models.JSONField()
    ledger_sha256 = models.CharField(max_length=64)
    observations = models.JSONField(default=dict, db_default={})
    observed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["journal", "generation"], name="gcp_app_apply_operation_generation"
            )
        ]

    def soft_delete(self, *, by=None):
        raise ValueError("GCP_APP_APPLY_RETIREMENT_REQUIRES_REVIEW")

    def restore(self):
        raise ValueError("GCP_APP_APPLY_RESTORE_REQUIRES_REVIEW")

    def save(self, *args, **kwargs):
        fields = (
            "organization_id",
            "journal_id",
            "deployment_id",
            "identity_source_id",
            "execution_receipt_id",
            "preparation_operation_id",
            "operation_id",
            "nonce",
            "generation",
            "workflow_id",
            "execution_id",
            "accepted_plan",
            "accepted_plan_sha256",
            "source_fence",
            "prepared_identity",
        )
        if self.pk:
            prior = type(self)._unscoped.filter(pk=self.pk).values(*fields).first()
            if prior and any(
                str(prior[k]) != str(getattr(self, k))
                if k in ("nonce", "operation_id")
                else prior[k] != getattr(self, k)
                for k in fields
            ):
                raise ValueError("GCP_APP_APPLY_ACCEPTED_METADATA_IMMUTABLE")
        return super().save(*args, **kwargs)
