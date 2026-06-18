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

Agent trigger binding (spec 33, PR-6):
WorkflowWebhook gains an optional ``agent_definition`` FK so the SAME
webhook row + SCM fan-out can bind to an agent ``Workload(kind=agent)``
instead of a ``WorkflowDefinition``. A bound webhook firing dispatches a
Task through the PR-1 ``runAstroliftAgent`` path with ``input_mapping``
applied to the incoming payload, rather than launching a WorkflowInstance.
Exactly one target is set per row (enforced by ``clean`` + a DB
``CheckConstraint``): ``workflow_definition`` XOR ``agent_definition``.
``workflow_definition`` is therefore nullable now (it was required); this
is a widening AlterField — existing rows keep their value and the XOR holds
for them (definition set, agent null). Condition triggers are explicitly
out of scope (spec §PR-6): there is no condition backing in the platform,
so only webhook/event bindings exist — a condition binding has no column.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
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
            f"WorkflowSchedule(def={self.workflow_definition_id}, " f"cron={self.cron_expression!r}, {state})"
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

    Agent binding (spec 33, PR-6)
    -----------------------------
    A webhook targets EITHER a ``workflow_definition`` (launch a
    WorkflowInstance) OR an ``agent_definition`` Workload (dispatch an
    AgentTask through the ``runAstroliftAgent`` path) — never both, never
    neither. The XOR is enforced by :meth:`clean` and the
    ``wfwebhook_exactly_one_target`` DB CheckConstraint. Both FKs are
    nullable so the column shape can represent either binding; the
    constraint keeps a row coherent.
    """

    # Nullable since PR-6: a webhook may instead target an agent (see
    # ``agent_definition``). The XOR constraint below guarantees exactly
    # one of the two is set, so a definition-bound row is unchanged.
    workflow_definition = models.ForeignKey(
        "workflows.WorkflowDefinition",
        on_delete=models.CASCADE,
        related_name="webhooks",
        null=True,
        blank=True,
    )
    # Agent-trigger binding (spec 33, PR-6). When set (and
    # ``workflow_definition`` is null), a firing of this webhook dispatches
    # an AgentTask for this agent ``Workload(kind=agent)`` via the PR-1
    # dispatch path, with ``input_mapping`` applied to the incoming payload.
    agent_definition = models.ForeignKey(
        "astrolift_registry.Workload",
        on_delete=models.CASCADE,
        related_name="agent_webhooks",
        null=True,
        blank=True,
        help_text=(
            "Agent Workload this webhook dispatches a Task for when it fires "
            "(spec 33, PR-6). Mutually exclusive with workflow_definition."
        ),
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
            "Key-mapping spec applied to the incoming payload. For a "
            "workflow_definition target it maps payload fields to the "
            "workflow's input schema; for an agent_definition target "
            "(PR-6) it shapes the dispatch payload handed to the agent "
            "Task (see services.workflow_triggers.apply_input_mapping)."
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
            # PR-6: cheap lookup of "agent webhooks bound to this agent".
            models.Index(
                fields=["agent_definition", "enabled"],
                name="wfwebhook_agent_enabled_idx",
            ),
        ]
        constraints = [
            # PR-6: exactly one target — a row binds to a workflow definition
            # XOR an agent. Encoded as "exactly one of the two FK columns is
            # non-null". Legacy rows (definition set, agent null) satisfy it.
            models.CheckConstraint(
                name="wfwebhook_exactly_one_target",
                condition=(
                    models.Q(workflow_definition__isnull=False, agent_definition__isnull=True)
                    | models.Q(workflow_definition__isnull=True, agent_definition__isnull=False)
                ),
            ),
        ]

    def clean(self) -> None:
        """Enforce the workflow-vs-agent XOR at the application layer too.

        The DB CheckConstraint is the hard guarantee; this surfaces a clear
        ValidationError on ``full_clean`` (used by admin / forms) instead of
        an IntegrityError. ``condition`` triggers are out of scope (spec
        §PR-6) — there is no third target column, so binding to neither is
        rejected here as well.
        """
        has_def = self.workflow_definition_id is not None
        has_agent = self.agent_definition_id is not None
        if has_def == has_agent:
            raise ValidationError(
                "WorkflowWebhook must target exactly one of "
                "workflow_definition or agent_definition (not both, not neither)."
            )

    def __str__(self) -> str:
        state = "enabled" if self.enabled else "disabled"
        target = (
            f"agent={self.agent_definition_id}"
            if self.agent_definition_id is not None
            else f"def={self.workflow_definition_id}"
        )
        return f"WorkflowWebhook(slug={self.slug!r}, {target}, {state})"
