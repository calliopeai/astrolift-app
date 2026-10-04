"""Append-only native reply identity, independent of current admission/completion."""

from django.db import models

from core.models.base import BaseCoreModel


class GCPIdentityAcknowledgement(BaseCoreModel):
    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.PROTECT)
    execution_receipt = models.ForeignKey(
        "astrolift_lifecycle.DeploymentExecutionReceipt", on_delete=models.PROTECT
    )
    preparation_journal = models.ForeignKey(
        "astrolift_services.GCPGKEPreparationJournal", on_delete=models.PROTECT, null=True
    )
    preparation_operation = models.ForeignKey(
        "astrolift_services.GCPGKEPreparationOperation", on_delete=models.PROTECT, null=True
    )
    iam_journal = models.ForeignKey(
        "astrolift_services.GCPWorkloadIdentityJournal", on_delete=models.PROTECT, null=True
    )
    kind = models.CharField(max_length=16, choices=[("PREPARATION", "Preparation"), ("IAM", "IAM")])
    operation_id = models.UUIDField()
    reservation_nonce = models.UUIDField()
    generation = models.PositiveBigIntegerField()
    submission_id = models.UUIDField()
    target_sha256 = models.CharField(max_length=64)
    accepted_union_sha256 = models.CharField(max_length=64)
    authority_reference_sha256 = models.CharField(max_length=64)
    submission_sha256 = models.CharField(max_length=64)
    binding_sha256 = models.CharField(max_length=64)
    acknowledgement = models.JSONField()
    acknowledgement_sha256 = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "operation_id", "submission_id"], name="gcp_ack_original_submission_unique"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        kind="PREPARATION",
                        preparation_journal__isnull=False,
                        preparation_operation__isnull=False,
                        iam_journal__isnull=True,
                    )
                    | models.Q(
                        kind="IAM",
                        preparation_journal__isnull=True,
                        preparation_operation__isnull=True,
                        iam_journal__isnull=False,
                    )
                ),
                name="gcp_ack_original_journal_shape",
            ),
            models.CheckConstraint(condition=models.Q(generation__gte=1), name="gcp_ack_generation_positive"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("GCP_ACKNOWLEDGEMENT_IMMUTABLE")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("GCP_ACKNOWLEDGEMENT_IMMUTABLE")

    def soft_delete(self, **kwargs):
        raise ValueError("GCP_ACKNOWLEDGEMENT_IMMUTABLE")

    def restore(self):
        raise ValueError("GCP_ACKNOWLEDGEMENT_IMMUTABLE")
