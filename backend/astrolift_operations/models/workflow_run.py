"""
WorkflowRun — the platform's mirror of a Temporal workflow.

Temporal owns the source of truth for workflow execution, but we keep
a lightweight mirror here for query performance, RBAC scoping, and
historical retention beyond Temporal's window. A reconciliation
worker keeps the mirror up to date.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class WorkflowRun(BaseCoreModel):
    class Status(models.TextChoices):
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"
        CANCELLED = "cancelled"
        TERMINATED = "terminated"
        TIMED_OUT = "timed_out"

    workflow_kind = models.CharField(max_length=128, db_index=True)
    workflow_id = models.CharField(max_length=255)
    run_id = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="workflow_runs",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    workflow_definition = models.ForeignKey(
        "workflows.WorkflowDefinition",
        related_name="execution_runs",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Definition executed by an agent workflow run; null for non-definition operations.",
    )
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="workflow_runs",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    app_environment = models.ForeignKey(
        "astrolift_lifecycle.AppEnvironment",
        related_name="workflow_runs",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    trigger_actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="triggered_workflow_runs",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    trigger_actor_token_kind = models.CharField(max_length=32, blank=True, default="")
    trigger_actor_token_id = models.BigIntegerField(null=True, blank=True)

    # Quick-access pointer for operator polling: which stage is running right now.
    # Null for workflows that have not yet started stage execution or for
    # top-level single-stage runs. Updated by the Temporal worker via the
    # Controller API (not direct DB writes from the worker process).
    current_stage_execution = models.ForeignKey(
        "workflows.WorkflowStageExecution",
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    result = models.JSONField(null=True, blank=True)
    failure = models.JSONField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workflow_id", "run_id"],
                name="workflowrun_id_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "-started_at"],
                name="wfr_org_started_idx",
            ),
        ]
