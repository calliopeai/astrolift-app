"""
astrolift_ci — in-platform CI runner data model (#870).

CiPipeline    the pipeline definition synced from .astrolift/ci.yaml
CiRun         a single execution of a CiPipeline (triggered by push/manual/schedule)
CiJob         one job within a CiRun (maps to a jobs: entry in ci.yaml)
CiStep        one step within a CiJob (maps to a steps: entry)
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class CiPipelineStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    DISABLED = "disabled", "Disabled"


class CiPipeline(BaseCoreModel):
    """A CI pipeline definition scoped to an organization."""

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="ci_pipelines",
        on_delete=models.CASCADE,
    )
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="ci_pipelines",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    slug = models.CharField(max_length=128)
    name = models.CharField(max_length=255)
    # Raw YAML from .astrolift/ci.yaml
    config_yaml = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=CiPipelineStatus.choices,
        default=CiPipelineStatus.ACTIVE,
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="cipipeline_slug_unique_active_per_org",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "status"],
                name="cipipeline_org_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"CiPipeline {self.slug} ({self.status})"


class CiRun(BaseCoreModel):
    """One execution of a CiPipeline."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    pipeline = models.ForeignKey(
        CiPipeline,
        related_name="runs",
        on_delete=models.CASCADE,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    trigger_kind = models.CharField(max_length=32)  # push | manual | schedule
    trigger_ref = models.CharField(max_length=512, blank=True, default="")  # branch or sha
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    conclusion = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["pipeline", "status"], name="cirun_pipeline_status_idx"),
        ]

    def __str__(self) -> str:
        return f"CiRun {self.guid} ({self.status})"


class CiJob(BaseCoreModel):
    """One job within a CiRun."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    run = models.ForeignKey(
        CiRun,
        related_name="jobs",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    runner_pool = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["run", "status"], name="cijob_run_status_idx"),
        ]

    def __str__(self) -> str:
        return f"CiJob {self.name} ({self.status})"


class CiStep(BaseCoreModel):
    """One step within a CiJob."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    job = models.ForeignKey(
        CiJob,
        related_name="steps",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=255)
    order = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    log_output = models.TextField(blank=True, default="")
    exit_code = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["order"]
        indexes = [
            models.Index(fields=["job", "order"], name="cistep_job_order_idx"),
        ]

    def __str__(self) -> str:
        return f"CiStep {self.order}: {self.name} ({self.status})"
