"""
WorkflowSchedule and WorkflowWebhook — trigger entry points for
WorkflowDefinition (#60).

WorkflowSchedule: cron-driven launch; maps to a Temporal schedule.
WorkflowWebhook: inbound HTTP event; verified via HMAC; maps to the
    ``POST /api/webhooks/workflow/<org>/<slug>`` endpoint.

Both models are append-only from the perspective of the platform:
enabling/disabling is done via the ``enabled`` flag, not deletion.
Soft-delete is not used here because webhook slugs must not be recycled
(an old, deleted webhook URL could be re-registered and confuse an
external system that still POSTs to it).
"""

from __future__ import annotations

from django.db import models


class WorkflowSchedule(models.Model):
    """Cron-based recurring trigger for a WorkflowDefinition.

    Each enabled schedule corresponds to exactly one Temporal schedule
    (keyed by ``temporal_schedule_id``). The Temporal side fires the
    workflow; the Controller's schedule listener creates a
    WorkflowInstance per firing.
    """

    workflow_definition = models.ForeignKey(
        "workflows.WorkflowDefinition",
        on_delete=models.CASCADE,
        related_name="schedules",
    )
    cron_expression = models.CharField(
        max_length=100,
        help_text="Standard 5-field cron expression (e.g. '0 9 * * 1-5').",
    )
    timezone = models.CharField(max_length=64, default="UTC")
    input_template = models.JSONField(
        default=dict,
        blank=True,
        help_text="Static input payload passed to each workflow instance.",
    )
    enabled = models.BooleanField(default=True, db_index=True)
    temporal_schedule_id = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Temporal schedule ID; empty when Temporal is disabled.",
    )
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["workflow_definition", "enabled"],
                name="wfschedule_defn_enabled_idx",
            ),
        ]

    def __str__(self) -> str:
        state = "enabled" if self.enabled else "disabled"
        return (
            f"WorkflowSchedule(def={self.workflow_definition_id}, "
            f"cron={self.cron_expression!r}, {state})"
        )


class WorkflowWebhook(models.Model):
    """Inbound-HTTP trigger for a WorkflowDefinition.

    The endpoint ``POST /api/webhooks/workflow/<org>/<slug>`` verifies the
    ``X-Astrolift-Signature`` HMAC header against ``secret_hash`` and then
    calls ``trigger_workflow_instance()``.

    ``secret_hash`` is SHA-256(plaintext_secret). The plaintext is shown
    once at creation and never stored. The endpoint uses
    ``hmac.compare_digest`` against this hash to verify the incoming
    ``X-Astrolift-Signature`` header.
    """

    workflow_definition = models.ForeignKey(
        "workflows.WorkflowDefinition",
        on_delete=models.CASCADE,
        related_name="webhooks",
    )
    slug = models.SlugField(
        max_length=64,
        unique=True,
        help_text="URL-safe identifier used in the endpoint path.",
    )
    secret_hash = models.CharField(
        max_length=64,
        help_text="SHA-256 hex digest of the signing secret; never store plaintext.",
    )
    input_mapping = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "JSONPath / key-mapping spec that maps incoming payload fields "
            "to the workflow's input schema."
        ),
    )
    enabled = models.BooleanField(default=True, db_index=True)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["slug", "enabled"], name="wfwebhook_slug_enabled_idx"),
        ]

    def __str__(self) -> str:
        state = "enabled" if self.enabled else "disabled"
        return f"WorkflowWebhook(slug={self.slug!r}, def={self.workflow_definition_id}, {state})"
