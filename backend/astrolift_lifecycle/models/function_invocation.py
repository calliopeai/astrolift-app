"""
FunctionInvocation — one execution of a ``kind: function`` workload.

A function invocation is created each time a function workload is triggered:
via HTTP request, queue message, webhook, or platform event. The record
captures the trigger context, HTTP metadata, and outcome — forming the
fleet-level invocation history surfaced on the Functions fleet page.

Unlike Tasks (operator-initiated batch jobs) and Agents (AI dispatch),
Functions are reactive: triggered by external events, scoped to
milliseconds-to-seconds, and scale to zero between invocations.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class FunctionInvocation(BaseCoreModel):
    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        SUCCESS = "success"
        ERROR = "error"

    class TriggerSource(models.TextChoices):
        HTTP = "http"
        EVENT = "event"
        SCHEDULE = "schedule"

    workload = models.ForeignKey(
        "astrolift_registry.Workload",
        related_name="function_invocations",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    app_environment = models.ForeignKey(
        "astrolift_lifecycle.AppEnvironment",
        related_name="function_invocations",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    trigger_source = models.CharField(
        max_length=32,
        choices=TriggerSource.choices,
        default=TriggerSource.HTTP,
    )
    # HTTP request context — populated for http and webhook triggers.
    http_method = models.CharField(max_length=10, blank=True, default="")
    http_path = models.TextField(blank=True, default="")
    http_status_code = models.IntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
    )
    error_message = models.TextField(blank=True, default="")
    # Invocation timing — duration_ms is the wall-clock time inside the
    # container from first byte in to last byte out.
    duration_ms = models.IntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    # The Knative / pod name that handled this invocation.
    k8s_pod_name = models.CharField(max_length=253, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(
                fields=["workload", "-created_at"],
                name="funcinv_workload_created_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"FunctionInvocation {self.guid} ({self.status})"
