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

SCM routing (#863):
WorkflowWebhook gains optional SCM filter fields so the push/PR
webhook handlers in auth1 and astrolift_scm can fan-out to matching
workflow triggers in addition to the core deploy path.

  scm_repo       — "owner/repo" exact match; blank matches any repo
  branch_pattern — glob-style branch filter (fnmatch); blank matches
                   any branch. "main", "feature/*", "*" are all valid.
  organization   — scopes the trigger to one org so lookups are
                   O(enabled webhooks per org) rather than a full-table
                   scan. Nullable for backward compat: rows created
                   before this column are org-agnostic and match any
                   org's SCM events when scm_repo is blank.
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

    SCM routing fields (#863)
    -------------------------
    When ``scm_repo`` or ``branch_pattern`` are set the webhook fires
    automatically for matching SCM push / PR events without requiring
    the SCM host to POST to this endpoint directly.  Leave both blank
    to fire on any SCM event delivered to the org.  Set only
    ``scm_repo`` to restrict to one repo regardless of branch.  Set
    only ``branch_pattern`` (glob) to restrict to branches across all
    repos.

    ``organization`` scopes the trigger so the push-handler can look
    up matching webhooks in O(rows per org) rather than scanning the
    full table.  Legacy rows without an org are excluded from SCM
    routing automatically.
    """

    workflow_definition = models.ForeignKey(
        "workflows.WorkflowDefinition",
        on_delete=models.CASCADE,
        related_name="webhooks",
    )
    # Source-host (SCM) webhooks scope to a RegisteredApp so the SCM
    # ingest receiver can find "which workflows fire for this app's
    # push/PR deliveries" without scanning every webhook in the system.
    # Null/blank for the org-level ``POST /api/webhooks/workflow/<org>/
    # <slug>`` webhooks, which resolve by slug and carry no app binding.
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="workflow_webhooks",
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

    # SCM routing (#863) — all three are nullable/blank for backward compat.
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="workflow_webhooks",
        help_text=(
            "Org that owns this trigger. Required for SCM routing; "
            "rows without an org are not matched by the SCM handler."
        ),
    )
    scm_repo = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text=(
            "SCM repo full name (owner/repo) this trigger matches. "
            "Blank matches any repo delivered to the org's webhook."
        ),
    )
    branch_pattern = models.CharField(
        max_length=256,
        blank=True,
        default="",
        help_text=(
            "Glob pattern matched against the pushed branch name (fnmatch). "
            "Blank matches any branch. Examples: 'main', 'release/*', '*'."
        ),
    )

    class Meta:
        indexes = [
            models.Index(fields=["slug", "enabled"], name="wfwebhook_slug_enabled_idx"),
            models.Index(
                fields=["organization", "enabled"],
                name="wfwebhook_org_enabled_idx",
            ),
            models.Index(
                fields=["registered_app", "enabled"],
                name="wfwebhook_app_enabled_idx",
            ),
        ]

    def __str__(self) -> str:
        state = "enabled" if self.enabled else "disabled"
        return f"WorkflowWebhook(slug={self.slug!r}, def={self.workflow_definition_id}, {state})"
