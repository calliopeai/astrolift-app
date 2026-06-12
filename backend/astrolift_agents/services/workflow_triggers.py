"""
Workflow trigger service — schedule, webhook, and direct launch entry
points for WorkflowDefinition (#60).

Three trigger types:

  create_scheduled_workflow_trigger(definition, cron_expression)
      Creates a Temporal schedule that fires the workflow on a cron. Each
      firing creates a new WorkflowInstance.

  create_webhook_workflow_trigger(definition, webhook_url)
      Registers a WorkflowWebhook row so the webhook receiver at
      POST /api/webhooks/workflow/<org>/<slug>/ can resolve the definition
      and launch an instance.

  trigger_workflow_instance(definition, input_data)
      The shared inner entry point used by both triggers (and manual
      launches). Creates a WorkflowInstance and enqueues a Temporal
      workflow run. Returns the WorkflowInstance.

All three write WorkflowInstance records when triggered.

Temporal schedule semantics
---------------------------
The Temporal Python SDK (>=1.0) supports ``client.create_schedule``.
Because the existing ``astrolift_workflows.client`` wraps Temporal in a
sync facade, we follow the same pattern: async implementation, wrapped
with ``async_to_sync``.

When Temporal is disabled (test / dev), ``trigger_workflow_instance``
writes the WorkflowInstance row but skips the Temporal dispatch; the
schedule helpers log a warning and return without creating a Temporal
schedule.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import hashlib
import logging
import secrets
import uuid

from django.db import transaction
from django.utils import timezone

from workflows.models import WorkflowDefinition, WorkflowInstance

log = logging.getLogger(__name__)

# Temporal workflow type for agent workflow definitions.
AGENT_WORKFLOW_TYPE = "AgentWorkflowDefinitionRun"


def trigger_workflow_instance(
    definition: WorkflowDefinition,
    input_data: dict | None = None,
    *,
    trigger_kind: str = "manual",
    triggered_by_user=None,
) -> WorkflowInstance:
    """Create a WorkflowInstance and start the Temporal workflow.

    This is the canonical inner entry point. Callers (schedule fired,
    webhook received, manual UI button) all converge here.

    Returns the created ``WorkflowInstance``.
    """
    with transaction.atomic():
        # WorkflowInstance.start() requires a content_object. For
        # definition-level triggers there is no domain object; we attach
        # the definition itself as the content_object.
        instance = WorkflowInstance.start(
            workflow=definition,
            obj=definition,
            user=triggered_by_user,
        )

    # Enqueue Temporal workflow (best-effort; instance row is already
    # committed so a Temporal failure is recoverable by re-triggering).
    _enqueue_temporal(instance, input_data or {}, trigger_kind=trigger_kind)

    return instance


def create_scheduled_workflow_trigger(
    definition: WorkflowDefinition,
    cron_expression: str,
    *,
    timezone_name: str = "UTC",
    input_template: dict | None = None,
    enabled: bool = True,
) -> dict:
    """Register a Temporal schedule that fires *definition* on a cron.

    Returns a dict with the schedule ID and next-fire hint:
        {"schedule_id": "...", "cron": "...", "enabled": True}

    When Temporal is disabled the schedule is logged but not created;
    the dict is still returned so callers can handle disabled mode
    uniformly.
    """
    schedule_id = _schedule_id(definition, cron_expression)

    from astrolift_workflows.client import _temporal_enabled

    if not _temporal_enabled():
        log.warning(
            "Temporal disabled — skipping schedule creation for definition %s cron '%s'",
            definition.pk,
            cron_expression,
        )
        return {"schedule_id": schedule_id, "cron": cron_expression, "enabled": False}

    try:
        _create_temporal_schedule(
            schedule_id=schedule_id,
            definition=definition,
            cron_expression=cron_expression,
            timezone_name=timezone_name,
            input_template=input_template or {},
            enabled=enabled,
        )
    except Exception:
        log.exception(
            "failed to create Temporal schedule for definition %s",
            definition.pk,
        )
        raise

    log.info(
        "created schedule %s for definition %s (cron=%r)",
        schedule_id,
        definition.pk,
        cron_expression,
    )
    return {"schedule_id": schedule_id, "cron": cron_expression, "enabled": enabled}


def create_webhook_workflow_trigger(
    definition: WorkflowDefinition,
    webhook_slug: str | None = None,
    *,
    input_mapping: dict | None = None,
) -> dict:
    """Register a WorkflowWebhook that fires *definition* on inbound POST.

    Creates a ``WorkflowWebhook`` record (or returns an existing active
    one for the same definition+slug). Returns a dict with the endpoint
    URL, signing secret (plaintext, shown once), and slug:

        {
            "slug": "...",
            "endpoint": "/api/webhooks/workflow/<org>/<slug>",
            "signing_secret": "<plaintext, shown once>",
        }

    The signing secret is stored hashed (SHA-256). The plaintext is
    returned here and must be shown to the operator immediately — it
    cannot be recovered afterward.
    """
    from astrolift_agents.models.workflow_trigger import WorkflowWebhook

    slug = webhook_slug or _random_slug()
    plaintext_secret = secrets.token_urlsafe(32)
    secret_hash = hashlib.sha256(plaintext_secret.encode()).hexdigest()

    try:
        org_slug = _resolve_org_slug(definition)
    except Exception:
        org_slug = "default"

    with transaction.atomic():
        hook = WorkflowWebhook.objects.create(
            workflow_definition=definition,
            slug=slug,
            secret_hash=secret_hash,
            input_mapping=input_mapping or {},
            enabled=True,
        )

    endpoint = f"/api/webhooks/workflow/{org_slug}/{slug}"
    log.info("created webhook %s → definition %s", slug, definition.pk)

    return {
        "slug": slug,
        "endpoint": endpoint,
        "signing_secret": plaintext_secret,
        "webhook_id": hook.pk,
    }


# ── SCM routing (#863) ───────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class ScmEvent:
    """Normalised SCM push or PR event for WorkflowWebhook routing."""

    organization_id: int
    repo_full_name: str
    branch: str
    head_sha: str
    event_kind: str  # "push" | "pull_request"
    # Additional payload forwarded as workflow input.
    raw_payload: dict = dataclasses.field(default_factory=dict)


def route_scm_push_to_workflow_webhooks(event: ScmEvent) -> list[WorkflowInstance]:
    """Fire all enabled WorkflowWebhooks whose SCM filters match *event*.

    Lookup is scoped to *event.organization_id* so only webhooks
    belonging to the delivering org are considered. The matching rules
    are:

    * ``scm_repo`` — exact match against ``event.repo_full_name``; blank
      means "match any repo".
    * ``branch_pattern`` — ``fnmatch`` glob against ``event.branch``;
      blank means "match any branch".

    For each matching webhook ``trigger_workflow_instance`` is called
    with ``trigger_kind="scm_<event_kind>"`` and the normalised SCM
    payload merged into the trigger input.  The ``last_triggered_at``
    timestamp is updated in a single bulk UPDATE after all instances
    are created so the loop stays O(1) DB round-trips per webhook.

    Returns the list of created ``WorkflowInstance`` objects (may be
    empty when no webhooks match).
    """
    from astrolift_agents.models.workflow_trigger import WorkflowWebhook

    candidates = list(
        WorkflowWebhook.objects.filter(
            organization_id=event.organization_id,
            enabled=True,
        ).select_related("workflow_definition")
    )

    instances: list[WorkflowInstance] = []
    fired_ids: list[int] = []

    for hook in candidates:
        if not _scm_event_matches(hook, event):
            continue
        input_data = {
            "scm_repo": event.repo_full_name,
            "branch": event.branch,
            "head_sha": event.head_sha,
            "event_kind": event.event_kind,
            **event.raw_payload,
        }
        try:
            instance = trigger_workflow_instance(
                hook.workflow_definition,
                input_data=input_data,
                trigger_kind=f"scm_{event.event_kind}",
            )
        except Exception:
            log.exception(
                "route_scm_push_to_workflow_webhooks: failed to trigger "
                "instance for webhook %s (def=%s)",
                hook.pk,
                hook.workflow_definition_id,
            )
            continue
        instances.append(instance)
        fired_ids.append(hook.pk)

    if fired_ids:
        now = timezone.now()
        WorkflowWebhook.objects.filter(pk__in=fired_ids).update(
            last_triggered_at=now,
            updated_at=now,
        )

    return instances


def _scm_event_matches(hook, event: ScmEvent) -> bool:
    """Return True when *hook*'s SCM filters are satisfied by *event*."""
    if hook.scm_repo and hook.scm_repo != event.repo_full_name:
        return False
    if hook.branch_pattern and not fnmatch.fnmatch(event.branch, hook.branch_pattern):
        return False
    return True


# ── Internal helpers ──────────────────────────────────────────────────────────


def _enqueue_temporal(
    instance: WorkflowInstance,
    input_data: dict,
    *,
    trigger_kind: str,
) -> None:
    """Submit the Temporal workflow start. Best-effort; never raises."""
    try:
        from astrolift_workflows.client import start_workflow

        workflow_id = f"wf-def-{instance.pk}-{uuid.uuid4().hex[:8]}"
        handle = start_workflow(
            AGENT_WORKFLOW_TYPE,
            args=[
                {
                    "workflow_instance_id": instance.pk,
                    "input": input_data,
                    "trigger_kind": trigger_kind,
                }
            ],
            workflow_id=workflow_id,
        )
        # Store the Temporal workflow_id on the instance for observability.
        if handle.enqueued:
            instance.temporal_workflow_id = handle.workflow_id
            instance.save(update_fields=["temporal_workflow_id", "updated_at"])
    except Exception:
        log.exception(
            "failed to enqueue Temporal workflow for instance %s", instance.pk
        )


def _schedule_id(definition: WorkflowDefinition, cron_expression: str) -> str:
    """Stable Temporal schedule ID derived from the definition PK + cron."""
    slug = getattr(definition, "slug", None) or str(definition.pk)
    cron_hash = hashlib.sha256(cron_expression.encode()).hexdigest()[:8]
    return f"astrolift-sched-{slug}-{cron_hash}"


def _create_temporal_schedule(
    *,
    schedule_id: str,
    definition: WorkflowDefinition,
    cron_expression: str,
    timezone_name: str,
    input_template: dict,
    enabled: bool,
) -> None:
    """Create a Temporal schedule (sync wrapper)."""
    from asgiref.sync import async_to_sync
    from astrolift_workflows.client import _get_client_async

    @async_to_sync
    async def _create():
        from temporalio.client import (
            Schedule,
            ScheduleActionStartWorkflow,
            ScheduleSpec,
            ScheduleCronString,
            ScheduleState,
        )

        client = await _get_client_async()
        action = ScheduleActionStartWorkflow(
            AGENT_WORKFLOW_TYPE,
            {
                "workflow_definition_id": definition.pk,
                "input": input_template,
                "trigger_kind": "scheduled",
            },
            id=f"{schedule_id}-run",
            task_queue=getattr(__import__("django.conf", fromlist=["settings"]).settings, "TEMPORAL_TASK_QUEUE", "astrolift-main"),
        )
        spec = ScheduleSpec(cron_strings=[ScheduleCronString(cron_expression)])
        await client.create_schedule(
            schedule_id,
            Schedule(action=action, spec=spec, state=ScheduleState(paused=not enabled)),
        )

    _create()


def _random_slug() -> str:
    return secrets.token_urlsafe(12).lower().replace("_", "").replace("-", "")[:16]


def _resolve_org_slug(definition: WorkflowDefinition) -> str:
    """Best-effort org slug for the webhook endpoint path."""
    # WorkflowDefinition doesn't carry an org FK in the current model;
    # fall back to "default" for the endpoint path placeholder.
    return "default"
