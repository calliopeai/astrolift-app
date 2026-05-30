"""
TaskRun — one execution of a ``kind: task`` workload.

Each manual trigger of a task workload (migration, seed script, data
export, one-off command) creates a TaskRun. The record is the fleet-level
execution history surfaced on the Tasks page.

Unlike ScheduledJobRun (which is created by the CronJob machinery),
TaskRun is always operator-initiated: via UI, CLI, or API.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class TaskRun(BaseCoreModel):
    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        CANCELLED = "cancelled"

    class TriggerKind(models.TextChoices):
        MANUAL = "manual"
        API = "api"
        WORKFLOW = "workflow"

    workload = models.ForeignKey(
        "astrolift_registry.Workload",
        related_name="task_runs",
        on_delete=models.PROTECT,
    )
    app_environment = models.ForeignKey(
        "astrolift_lifecycle.AppEnvironment",
        related_name="task_runs",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    trigger_kind = models.CharField(
        max_length=32,
        choices=TriggerKind.choices,
        default=TriggerKind.MANUAL,
    )
    triggered_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="task_runs",
    )
    # The actual command array executed in the container.
    command = models.JSONField(default=list)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
    )
    exit_code = models.IntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.IntegerField(null=True, blank=True)
    # The batch/v1 Job name in the cluster — used to stream logs and
    # poll completion status.
    k8s_job_name = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(
                fields=["workload", "-created_at"],
                name="taskrun_workload_created_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"TaskRun {self.guid} ({self.status})"
