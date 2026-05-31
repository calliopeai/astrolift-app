"""
AgentRun — one dispatch/execution of a ``kind: agent`` workload.

When an operator dispatches an AI agent workload, an AgentRun is created
to record the input, track execution status, and store the result (including
reasoning trace URL). The record is the fleet-level execution history
surfaced on the Agents fleet page.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class AgentRun(BaseCoreModel):
    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        CANCELLED = "cancelled"

    class TriggerKind(models.TextChoices):
        MANUAL = "manual"
        API = "api"
        SCHEDULED = "scheduled"
        EVENT = "event"

    workload = models.ForeignKey(
        "astrolift_registry.Workload",
        related_name="agent_runs",
        on_delete=models.PROTECT,
    )
    app_environment = models.ForeignKey(
        "astrolift_lifecycle.AppEnvironment",
        related_name="agent_runs",
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
        related_name="agent_runs",
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
    )
    # Structured input provided to the agent at dispatch time.
    input = models.JSONField(null=True, blank=True)
    # Structured output / result produced by the agent.
    output = models.JSONField(null=True, blank=True)
    # URL to the agent's reasoning trace (OTel trace or custom store).
    reasoning_trace_url = models.CharField(max_length=1024, blank=True, default="")
    tool_calls_count = models.IntegerField(default=0)
    retry_count = models.IntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.IntegerField(null=True, blank=True)
    k8s_pod_name = models.CharField(max_length=255, blank=True, default="")
    # Result retention TTL — AgentRun records older than this should be
    # pruned by a scheduled sweep. Copied from Workload.result_ttl_hours
    # at dispatch time so changes to the workload don't retroactively alter
    # existing run retention policies.
    result_ttl_hours = models.IntegerField(default=72)
    # Streaming log excerpt from the Dispatch Service (#51).
    # Ring buffer capped at 10k lines by the log_collector service.
    # Full log is in object storage; this field is the live-view source.
    log_excerpt = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            models.Index(
                fields=["workload", "-created_at"],
                name="agentrun_workload_created_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"AgentRun {self.guid} ({self.status})"
