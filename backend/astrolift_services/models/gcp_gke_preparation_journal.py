"""Private original GKE subject ownership and committed preparation metadata."""

from django.db import models

from core.models.base import BaseCoreModel


class GCPGKEPreparationJournal(BaseCoreModel):
    class State(models.TextChoices):
        RESERVED = "RESERVED"
        UNSENT = "UNSENT"
        SENT = "SENT"
        UNKNOWN = "UNKNOWN"
        PREPARED = "PREPARED"
        OBSERVED = "OBSERVED"

    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.PROTECT)
    registered_app = models.ForeignKey("astrolift_registry.RegisteredApp", on_delete=models.PROTECT)
    tenant_cluster = models.ForeignKey("astrolift_clusters.TenantCluster", on_delete=models.PROTECT)
    provider_plugin = models.ForeignKey("astrolift_clusters.ProviderPlugin", on_delete=models.PROTECT)
    target_snapshot = models.JSONField()
    target_sha256 = models.CharField(max_length=64)
    preparation_sha256 = models.CharField(max_length=64)
    context_sha256 = models.CharField(max_length=64)
    ledger = models.JSONField()
    ledger_sha256 = models.CharField(max_length=64)
    operation_id = models.UUIDField()
    reservation_nonce = models.UUIDField()
    generation = models.PositiveBigIntegerField(default=1, db_default=1)
    desired_revision = models.PositiveBigIntegerField()
    prepared_revision = models.PositiveBigIntegerField(default=0, db_default=0)
    observed_revision = models.PositiveBigIntegerField(default=0, db_default=0)
    accepted_union_template_sha256 = models.CharField(max_length=64)
    derived_native_union_sha256 = models.CharField(max_length=64, blank=True, default="")
    authority_reference_sha256 = models.CharField(max_length=64)
    workflow_id = models.CharField(max_length=200)
    execution_id = models.CharField(max_length=200)
    state = models.CharField(
        max_length=16, choices=State.choices, default=State.RESERVED, db_default=State.RESERVED
    )
    submissions = models.JSONField(default=dict, db_default={})
    prepared_at = models.DateTimeField(null=True, blank=True)
    observed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["registered_app", "tenant_cluster"],
                condition=models.Q(deleted_at__isnull=True),
                name="gcp_preparation_journal_live_owner",
            ),
            models.CheckConstraint(
                condition=models.Q(generation__gte=1), name="gcp_preparation_generation_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(desired_revision__gte=1), name="gcp_preparation_revision_positive"
            ),
        ]

    def soft_delete(self, *, by=None):
        raise ValueError("GCP_PREPARATION_RETIREMENT_REQUIRES_REVIEW")

    def restore(self):
        raise ValueError("GCP_PREPARATION_RESTORE_REQUIRES_REVIEW")


class GCPGKEPreparationOperation(BaseCoreModel):
    """Protected accepted metadata; completion never replaces the original plan."""

    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.PROTECT)
    journal = models.ForeignKey(GCPGKEPreparationJournal, on_delete=models.PROTECT, related_name="operations")
    operation_id = models.UUIDField(unique=True)
    reservation_nonce = models.UUIDField()
    generation = models.PositiveBigIntegerField()
    desired_revision = models.PositiveBigIntegerField()
    accepted_union_template = models.JSONField()
    accepted_union_template_sha256 = models.CharField(max_length=64)
    accepted_authority_reference = models.JSONField()
    authority_reference_sha256 = models.CharField(max_length=64)
    workflow_id = models.CharField(max_length=200)
    execution_id = models.CharField(max_length=200)
    derived_native_union_sha256 = models.CharField(max_length=64, blank=True, default="")
    state = models.CharField(max_length=16, default="RESERVED", db_default="RESERVED")
    submissions = models.JSONField(default=dict, db_default={})
    ledger = models.JSONField(default=dict, db_default={})
    ledger_sha256 = models.CharField(max_length=64)
    prepared_at = models.DateTimeField(null=True, blank=True)
    observed_at = models.DateTimeField(null=True, blank=True)
    completed_iam_journal_id = models.UUIDField(null=True, blank=True)
    completed_iam_journal_version = models.PositiveBigIntegerField(null=True, blank=True)
    completed_iam_generation = models.PositiveBigIntegerField(null=True, blank=True)
    completed_iam_ledger_sha256 = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["journal", "generation"], name="gcp_preparation_operation_generation"
            ),
            models.CheckConstraint(
                condition=models.Q(generation__gte=1), name="gcp_preparation_op_generation_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(desired_revision__gte=1), name="gcp_preparation_op_revision_positive"
            ),
        ]

    def soft_delete(self, *, by=None):
        raise ValueError("GCP_PREPARATION_OPERATION_RETIREMENT_REFUSED")

    def restore(self):
        raise ValueError("GCP_PREPARATION_OPERATION_RESTORE_REFUSED")

    def delete(self, *args, **kwargs):
        raise ValueError("GCP_PREPARATION_OPERATION_ERASURE_REFUSED")

    def save(self, *args, **kwargs):
        immutable = (
            "organization_id",
            "journal_id",
            "operation_id",
            "reservation_nonce",
            "generation",
            "desired_revision",
            "accepted_union_template",
            "accepted_union_template_sha256",
            "accepted_authority_reference",
            "authority_reference_sha256",
            "workflow_id",
            "execution_id",
        )
        if self.pk:
            prior = (
                type(self)
                ._unscoped.filter(pk=self.pk)
                .values(*immutable, "derived_native_union_sha256")
                .first()
            )
            if prior and any(
                (
                    str(prior[name]) != str(getattr(self, name))
                    if name in ("operation_id", "reservation_nonce")
                    else prior[name] != getattr(self, name)
                )
                for name in immutable
            ):
                raise ValueError("GCP_PREPARATION_ACCEPTED_METADATA_IMMUTABLE")
            if (
                prior
                and prior["derived_native_union_sha256"]
                and prior["derived_native_union_sha256"] != self.derived_native_union_sha256
            ):
                raise ValueError("GCP_PREPARATION_NATIVE_UNION_IMMUTABLE")
        return super().save(*args, **kwargs)
