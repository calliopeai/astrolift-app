"""Private append-only enqueue intent and actual Temporal run provenance."""

from django.db import models

from core.models.base import BaseCoreModel


class DeploymentExecutionReceipt(BaseCoreModel):
    class Kind(models.TextChoices):
        EXPECTED = "EXPECTED"
        BOUND = "BOUND"

    deployment = models.ForeignKey("astrolift_lifecycle.Deployment", on_delete=models.PROTECT)
    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.PROTECT)
    origin = models.ForeignKey("astrolift_lifecycle.DeploymentIdentityOrigin", on_delete=models.PROTECT)
    expected = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    workflow_id = models.CharField(max_length=256)
    workflow_type = models.CharField(max_length=64)
    run_id = models.CharField(max_length=64, blank=True)
    input_sha256 = models.CharField(max_length=64)
    operation_uuid = models.UUIDField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["deployment", "kind"], name="deployment_execution_kind_unique"),
            models.CheckConstraint(
                condition=(
                    models.Q(kind="EXPECTED", expected__isnull=True, run_id="")
                    | (models.Q(kind="BOUND", expected__isnull=False) & ~models.Q(run_id=""))
                ),
                name="deployment_execution_receipt_shape",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("DEPLOYMENT_EXECUTION_IMMUTABLE")
        return super().save(*args, **kwargs)

    def soft_delete(self, **kwargs):
        raise ValueError("DEPLOYMENT_EXECUTION_IMMUTABLE")

    def restore(self):
        raise ValueError("DEPLOYMENT_EXECUTION_IMMUTABLE")
