"""
TaskMeteringRecord — compute usage snapshot for one AgentRun (#56).

The dispatcher-authenticated POST /api/dispatch/v1/tasks/<id>/meter/ accepts
an operational usage report for a run in the dispatcher's organization.
One record per run is enforced by the model; completion callbacks can use
its reported token counts when the task's completion metadata lacks them.
The retired ``agentUsageSummary`` API no longer reads this table. A stored
record does not establish complete runtime collection, automatic backfill,
monetary attribution or quota enforcement.
"""

from __future__ import annotations

from django.db import models


class TaskMeteringRecord(models.Model):
    """One compute-usage snapshot per AgentRun.

    ``agent_run`` is a OneToOne so a task never accumulates multiple rows.
    The model is intentionally plain (no BaseCoreModel) — it is append-only
    and never soft-deleted; audit is implicit via ``created_at``.
    """

    class MeteringSource(models.TextChoices):
        K8S_METRICS = "k8s_metrics", "Kubernetes Metrics"
        ECS_METADATA = "ecs_metadata", "ECS Task Metadata"
        WALL_TIME_ESTIMATE = "wall_time_estimate", "Wall-Time Estimate"
        AGENT_REPORTED = "agent_reported", "Agent-Reported"

    agent_run = models.OneToOneField(
        "astrolift_lifecycle.AgentRun",
        on_delete=models.CASCADE,
        related_name="metering_record",
        db_index=True,
    )
    # Wall time is derived from AgentRun.started_at / ended_at but
    # stored here for fast aggregation without joins.
    wall_seconds = models.IntegerField(
        null=True,
        blank=True,
        help_text="ended_at - started_at in whole seconds; null if task never started.",
    )
    cpu_seconds = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="CPU-time consumed (as reported by the runtime or estimated).",
    )
    memory_peak_mb = models.IntegerField(
        null=True,
        blank=True,
        help_text="Peak RSS in MB; null when the runtime doesn't expose it.",
    )
    token_input = models.IntegerField(
        null=True,
        blank=True,
        help_text="LLM input tokens consumed; null for non-LLM tasks.",
    )
    token_output = models.IntegerField(
        null=True,
        blank=True,
        help_text="LLM output tokens generated; null for non-LLM tasks.",
    )
    metering_source = models.CharField(
        max_length=32,
        choices=MeteringSource.choices,
        default=MeteringSource.WALL_TIME_ESTIMATE,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["created_at"], name="taskmeter_created_idx"),
        ]

    def __str__(self) -> str:
        return (
            f"TaskMeteringRecord(run={self.agent_run_id}, "
            f"wall={self.wall_seconds}s, src={self.metering_source})"
        )
