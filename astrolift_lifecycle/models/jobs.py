"""
Per-execution records for cron / one-shot work.

* ScheduledJobRun: one execution of a cronjob workload.
* CommandRun: one ``astro app exec`` (or equivalent) invocation; lets
  operators reconstruct what was run on a pod and capture output.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class ScheduledJobRun(BaseCoreModel):
    class Status(models.TextChoices):
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        SUPERSEDED = "superseded"

    workload = models.ForeignKey(
        "astrolift_registry.Workload",
        related_name="scheduled_runs",
        on_delete=models.CASCADE,
    )
    app_environment = models.ForeignKey(
        "astrolift_lifecycle.AppEnvironment",
        related_name="scheduled_runs",
        on_delete=models.CASCADE,
    )
    k8s_job_name = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.IntegerField(null=True, blank=True)
    exit_code = models.IntegerField(null=True, blank=True)
    log_excerpt = models.TextField(blank=True, default="")


class CommandRun(BaseCoreModel):
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="command_runs",
        on_delete=models.CASCADE,
    )
    workload = models.ForeignKey(
        "astrolift_registry.Workload",
        related_name="command_runs",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    invoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="invoked_command_runs",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    command = models.JSONField(default=list, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    exit_code = models.IntegerField(null=True, blank=True)
    log_excerpt = models.TextField(blank=True, default="")
