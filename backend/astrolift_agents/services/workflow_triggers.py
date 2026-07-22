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

from django.db import transaction
from django.utils import timezone

from workflows.models import WorkflowDefinition, WorkflowInstance

log = logging.getLogger(__name__)

# Temporal workflow type for agent workflow definitions. Must match the name
# the worker registers (`@workflow.defn(name=...)` in
# astrolift_workflows/workflows/workflow_definition_run.py); a name no worker
# registers leaves the started run unclaimed and the workflow never executes
# (#1025).
AGENT_WORKFLOW_TYPE = "WorkflowDefinitionRunWorkflow"


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


# ── Agent trigger binding (spec 33, PR-6) ────────────────────────────────────


def apply_input_mapping(payload: dict | None, input_mapping: dict | None) -> dict:
    """Shape an incoming webhook ``payload`` into an agent dispatch payload.

    The contract, kept deliberately small (spec 33, PR-6):

      * **Empty / falsy ``input_mapping``** → pass the whole ``payload``
        through verbatim (mirrors the workflow-definition path, where the
        definition's own input schema projects the raw payload downstream).
      * **Non-empty mapping** → it is a ``{out_key: source_path}`` spec. Each
        ``source_path`` is a dotted lookup into ``payload``
        (``"pull_request.number"`` walks ``payload["pull_request"]["number"]``);
        a missing path yields ``None`` for that key rather than raising, so a
        partial payload still dispatches. The result is exactly the mapped
        keys — nothing else from the payload leaks through.

    Returns a plain ``dict`` suitable as the agent Task's trigger payload.
    """
    payload = payload or {}
    if not input_mapping:
        return dict(payload)
    out: dict = {}
    for out_key, source_path in input_mapping.items():
        # Only string source paths are walked; a non-string mapping value is
        # treated as a literal (lets a mapping inject a constant if desired).
        if not isinstance(source_path, str):
            out[out_key] = source_path
            continue
        cursor: object = payload
        for segment in source_path.split("."):
            if isinstance(cursor, dict) and segment in cursor:
                cursor = cursor[segment]
            else:
                cursor = None
                break
        out[out_key] = cursor
    return out


def dispatch_agent_task_from_webhook(webhook, payload: dict | None) -> object | None:
    """Dispatch an AgentTask for an agent-bound ``WorkflowWebhook`` firing.

    The agent counterpart of :func:`trigger_workflow_instance`: the SCM
    fan-out (and the app-scoped ingest path) call this when a fired webhook
    targets an ``agent_definition`` Workload instead of a WorkflowDefinition.

    It MIRRORS the PR-1 ``runAstroliftAgent`` dispatch path exactly — creates
    an ``AgentTask`` with ``agent_definition`` set, advances DRAFT→QUEUED via
    the sanctioned ``transition_to``, then enqueues ``DispatchAgentTaskWorkflow``
    keyed to the task guid — rather than reinventing dispatch. The incoming
    payload shaped by the binding's ``input_mapping`` (``apply_input_mapping``)
    rides on the dispatch input's ``trigger_payload`` so the run carries the
    mapped input (PR-1 manual/cron/loop dispatch leaves it ``None``).

    Honors ``run_paused`` (acceptance (3)): a paused agent's bound webhook is a
    no-op (returns None) — the kill-switch halts trigger dispatch the same way
    it halts the loop + cron ticks.

    Returns the created ``AgentTask`` (so callers can count / observe), or
    ``None`` when nothing was dispatched (no agent target, paused, or the
    agent/app was soft-deleted out from under the binding).

    Condition triggers are explicitly out of scope (spec §PR-6): a webhook can
    only bind to an agent for webhook/event delivery — there is no condition
    backing in the platform, so no condition path exists here.
    """
    from django.db import transaction

    from astrolift_agents.models import AgentTask
    from astrolift_registry.models import Workload
    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import Actor, DispatchAgentTaskInput

    workload = getattr(webhook, "agent_definition", None)
    if workload is None:
        return None
    # Re-validate the binding target at fire time: the agent must still be a
    # live agent Workload under a live app (the FK is CASCADE, but a soft
    # delete leaves the row — honour soft-delete here so a torn-down agent
    # stops dispatching, matching the loop/cron selectors).
    if (
        workload.kind != Workload.Kind.AGENT
        or workload.deleted_at is not None
        or getattr(workload, "registered_app", None) is None
        or workload.registered_app.deleted_at is not None
    ):
        return None
    if workload.run_paused:
        # Operator kill-switch — a paused agent's trigger does not dispatch.
        return None

    mapped = apply_input_mapping(payload, getattr(webhook, "input_mapping", None))

    org_id = workload.registered_app.organization_id
    with transaction.atomic():
        task = AgentTask.objects.create(
            organization_id=org_id,
            agent_definition=workload,
            status=AgentTask.Status.DRAFT,
            timeout_seconds=int(workload.tool_timeout_seconds or 300),
            # Freeze the mapped webhook payload on the task so the spawner
            # surfaces it to the pod as ASTROLIFT_TRIGGER_PAYLOAD (#930).
            dispatch_input=mapped or None,
        )
        task.transition_to(AgentTask.Status.QUEUED)

    actor = Actor(kind="system", display="agent-trigger")
    start_workflow(
        "DispatchAgentTaskWorkflow",
        args=[
            DispatchAgentTaskInput(
                agent_task_id=task.pk,
                actor=actor,
                trigger_payload=mapped,
            )
        ],
        workflow_id=f"DispatchAgentTaskWorkflow-{task.guid}",
    )
    return task


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
    organization=None,
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

    ``organization`` scopes the webhook (and the endpoint path). The webhook
    is owned by whoever creates it — the caller's org — because
    WorkflowDefinition carries no org FK; the org-level endpoint resolves the
    webhook by ``<org>/<slug>`` and verifies the org matches, so an org is
    required for the endpoint to be reachable. The signing secret is stored
    hashed (SHA-256); the plaintext is returned once and not recoverable.
    """
    from astrolift_agents.models.workflow_trigger import WorkflowWebhook

    slug = webhook_slug or _random_slug()
    plaintext_secret = secrets.token_urlsafe(32)
    secret_hash = hashlib.sha256(plaintext_secret.encode()).hexdigest()

    if organization is not None:
        org_slug = organization.slug
    else:
        try:
            org_slug = _resolve_org_slug(definition)
        except Exception:
            org_slug = "default"

    with transaction.atomic():
        hook = WorkflowWebhook.objects.create(
            workflow_definition=definition,
            organization=organization,
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


def create_agent_webhook_trigger(
    workload,
    webhook_slug: str | None = None,
    *,
    input_mapping: dict | None = None,
    scm_repo: str = "",
    branch_pattern: str = "",
) -> dict:
    """Register a ``WorkflowWebhook`` that dispatches *workload* (an agent
    Workload) on inbound POST to ``/api/webhooks/workflow/<org>/<slug>``.

    The agent-bound sibling of ``create_webhook_workflow_trigger`` — the
    inbound dispatch side (``dispatch_agent_task_from_webhook``) and the
    model's ``agent_definition`` FK already exist (spec 33, PR-6); this is
    the missing creation seam (#983). Same secret contract: stored as
    SHA-256(plaintext), plaintext returned once. The webhook is org-scoped
    (resolved by ``<org>/<slug>``).

    ``scm_repo`` / ``branch_pattern`` are the SCM fan-out filters (#863): set
    them and the binding ALSO fires automatically on matching push/PR events
    routed to the org (``route_scm_push_to_workflow_webhooks``), not only on a
    direct POST to the endpoint. Both blank = direct-POST only / match-any.
    """
    from astrolift_agents.models.workflow_trigger import WorkflowWebhook

    org = workload.registered_app.organization
    slug = webhook_slug or _random_slug()
    plaintext_secret = secrets.token_urlsafe(32)
    secret_hash = hashlib.sha256(plaintext_secret.encode()).hexdigest()

    with transaction.atomic():
        hook = WorkflowWebhook.objects.create(
            agent_definition=workload,
            organization=org,
            slug=slug,
            secret_hash=secret_hash,
            input_mapping=input_mapping or {},
            scm_repo=scm_repo or "",
            branch_pattern=branch_pattern or "",
            enabled=True,
        )

    endpoint = f"/api/webhooks/workflow/{org.slug}/{slug}"
    log.info("created agent webhook %s → workload %s", slug, workload.pk)

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

    For each matching webhook, the target decides the dispatch
    (spec 33, PR-6):
      * a ``workflow_definition``-bound webhook calls
        ``trigger_workflow_instance`` (launch a WorkflowInstance), as before;
      * an ``agent_definition``-bound webhook calls
        ``dispatch_agent_task_from_webhook`` (dispatch an AgentTask through
        the PR-1 path, with ``input_mapping`` applied to the SCM payload).
    Both honour ``trigger_kind="scm_<event_kind>"`` semantics and the
    per-webhook failure isolation. The ``last_triggered_at`` timestamp is
    updated in a single bulk UPDATE after all dispatches so the loop stays
    O(1) DB round-trips per webhook.

    Returns the list of created ``WorkflowInstance`` objects (may be empty
    when no webhooks match or when every match was agent-bound — agent Tasks
    are not WorkflowInstances). Agent dispatches still update
    ``last_triggered_at`` and are isolated from each other.
    """
    from astrolift_agents.models.workflow_trigger import WorkflowWebhook

    candidates = list(
        WorkflowWebhook.objects.filter(
            organization_id=event.organization_id,
            enabled=True,
        ).select_related("workflow_definition", "agent_definition__registered_app")
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
        # Agent-bound webhook (PR-6): dispatch a Task, not a WorkflowInstance.
        if hook.agent_definition_id is not None:
            try:
                task = dispatch_agent_task_from_webhook(hook, input_data)
            except Exception:
                log.exception(
                    "route_scm_push_to_workflow_webhooks: failed to dispatch "
                    "agent task for webhook %s (agent=%s)",
                    hook.pk,
                    hook.agent_definition_id,
                )
                continue
            # A paused / torn-down agent returns None — not a fire, so don't
            # stamp last_triggered_at for it.
            if task is not None:
                fired_ids.append(hook.pk)
            continue
        try:
            instance = trigger_workflow_instance(
                hook.workflow_definition,
                input_data=input_data,
                trigger_kind=f"scm_{event.event_kind}",
            )
        except Exception:
            log.exception(
                "route_scm_push_to_workflow_webhooks: failed to trigger instance for webhook %s (def=%s)",
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
    """Start the WorkflowDefinition stage executor for *instance*.

    Best-effort; never raises (the instance row is already committed, so a
    Temporal failure is recoverable by re-triggering).

    The executor (``WorkflowDefinitionRunWorkflow``) takes a
    ``WorkflowDefinitionRunInput`` dataclass and keys its stage executions to
    an ``astrolift_operations.WorkflowRun`` mirror-row pk — passing a plain
    dict left the workflow's ``run`` unable to deserialize its argument and
    the triggered run never started (#1030). Dispatch goes through the shared
    ``start_workflow_definition_run`` entry point — the same one
    ``runWorkflowDefinition`` and the inbound webhook use (#1020) — so the
    mirror row exists and the dispatched argument matches the workflow's
    ``run`` signature.
    """
    try:
        from astrolift_workflows.inputs import Actor
        from workflows.run_service import start_workflow_definition_run

        _run, workflow_id = start_workflow_definition_run(
            instance.workflow,
            trigger_payload=input_data,
            actor=Actor(kind="system", user_id=None, display=trigger_kind),
        )
        # Store the Temporal workflow_id + run_id on the instance — the latter
        # lets a historical run's DAG overlay without a live describe (#1180).
        instance.temporal_workflow_id = workflow_id
        instance.temporal_run_id = _run.run_id or None
        instance.save(update_fields=["temporal_workflow_id", "temporal_run_id", "updated_at"])
    except Exception:
        log.exception("failed to enqueue Temporal workflow for instance %s", instance.pk)


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
    """Create a Temporal schedule (sync wrapper).

    The schedule fires ``WorkflowDefinitionRunWorkflow``, whose
    ``run(self, input: WorkflowDefinitionRunInput)`` expects the dataclass —
    passing a plain dict left ``input.workflow_run_id`` undefined and every
    scheduled fire failed to start (#1036). We build the executor input the
    same way the inline trigger path does (``build_workflow_definition_run_input``
    creates the ``WorkflowRun`` mirror row + ``workflow_run_id`` the dataclass
    needs) and carry that ``WorkflowDefinitionRunInput`` as the schedule
    action's argument, so each fire deserializes correctly. The action's
    workflow id mirrors the run pk (the executor's id convention), so a
    re-fire joins the in-flight run rather than spawning a parallel one.
    """
    from asgiref.sync import async_to_sync
    from django.conf import settings

    from astrolift_workflows.client import _get_client_async
    from astrolift_workflows.inputs import Actor
    from workflows.run_service import build_workflow_definition_run_input

    _run, run_input, run_workflow_id = build_workflow_definition_run_input(
        definition,
        trigger_payload=input_template,
        actor=Actor(kind="system", user_id=None, display="scheduled"),
    )
    task_queue = getattr(settings, "TEMPORAL_TASK_QUEUE", "astrolift-main")

    @async_to_sync
    async def _create():
        from temporalio.client import (
            Schedule,
            ScheduleActionStartWorkflow,
            ScheduleSpec,
            ScheduleState,
        )

        client = await _get_client_async()
        action = ScheduleActionStartWorkflow(
            AGENT_WORKFLOW_TYPE,
            run_input,
            id=run_workflow_id,
            task_queue=task_queue,
        )
        spec = ScheduleSpec(
            cron_expressions=[cron_expression],
            time_zone_name=timezone_name,
        )
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
