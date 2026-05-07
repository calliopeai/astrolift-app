"""
PreviewEnvironment — a per-PR ephemeral deploy.

Created when ``preview_enabled`` is true on the registered app and a
PR is opened. Hostname follows ``pr-{n}-{slug}.pr.{org}.{base-zone}``;
namespace is segregated per preview. Torn down on PR close (with a
short grace period for re-open).
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class PreviewEnvironment(BaseCoreModel):
    class Status(models.TextChoices):
        BUILDING = "building"
        RUNNING = "running"
        FAILED = "failed"
        TORN_DOWN = "torn_down"

    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="preview_environments",
        on_delete=models.CASCADE,
    )
    pr_number = models.PositiveIntegerField()
    branch = models.CharField(max_length=255)
    commit_sha = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.BUILDING)
    hostname = models.CharField(max_length=255)
    namespace = models.CharField(max_length=128)
    app_environment = models.ForeignKey(
        "astrolift_lifecycle.AppEnvironment",
        related_name="preview_environments",
        on_delete=models.CASCADE,
    )
    last_deployed_at = models.DateTimeField(null=True, blank=True)
    torn_down_at = models.DateTimeField(null=True, blank=True)
    workflow_run = models.ForeignKey(
        "astrolift_operations.WorkflowRun",
        related_name="preview_environments",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["registered_app", "pr_number"],
                condition=models.Q(deleted_at__isnull=True),
                name="preview_pr_unique_active_per_app",
            ),
        ]
