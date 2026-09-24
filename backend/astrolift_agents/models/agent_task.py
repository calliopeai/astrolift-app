"""
AgentTask — the discrete unit of agent dispatch.

Ephemeral, org-scoped, with a lifecycle state machine covering the full
dispatch path from creation to terminal outcome.

State machine (issue #44):

    DRAFT → QUEUED: explicit enqueue mutation (or auto on Brief READY)
    QUEUED → PROVISIONING: dispatcher selected + spawn request sent
    PROVISIONING → RUNNING: Dispatch Service confirms container started
    RUNNING → COMPLETED | FAILED | TIMED_OUT | CANCELLED: terminal
    CANCELLED from RUNNING is allowed only after the caller sends a stop
    signal to the Dispatcher; ``transition_to`` records the resulting state.

The allowed graph is encoded in ``_TRANSITIONS`` — ``transition_to`` is
the only sanctioned way to advance status.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from core.models.base import BaseCoreModel


class AgentTask(BaseCoreModel):
    class Status(models.TextChoices):
        DRAFT = "draft"
        QUEUED = "queued"
        PROVISIONING = "provisioning"
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"
        TIMED_OUT = "timed_out"
        CANCELLED = "cancelled"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="agent_tasks",
        on_delete=models.CASCADE,
    )
    team = models.ForeignKey(
        "astrolift_identity.Team",
        related_name="agent_tasks",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    project = models.ForeignKey(
        "astrolift_identity.Project",
        related_name="agent_tasks",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # FK to the agent-tier Workload that defines the container image +
    # runtime config.  Nullable so a task can survive workload deletion.
    agent_definition = models.ForeignKey(
        "astrolift_registry.Workload",
        related_name="agent_tasks",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    brief = models.ForeignKey(
        "astrolift_agents.Brief",
        related_name="agent_tasks",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # Set at PROVISIONING when the Controller selects a DispatcherInstance.
    dispatcher = models.ForeignKey(
        "astrolift_agents.DispatcherInstance",
        related_name="agent_tasks",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # Explicit link to the fleet-history AgentRun this task was dispatched
    # for (#1217). Set at dispatch when both records exist (the workflow
    # stage executor) and backfilled by the reconciler when the historical
    # ``(workload, external_id == k8s_pod_name)`` join resolves. Nullable:
    # push-mode / pre-spawn runs and non-workflow dispatch paths have no
    # AgentRun, and rows created before this field just stay null until the
    # next reconcile. Consumers must go through ``resolve_agent_task_for_run``
    # so the historical fuzzy join keeps working while the FK backfills.
    agent_run = models.OneToOneField(
        "astrolift_lifecycle.AgentRun",
        related_name="agent_task",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    # Persist placement before external creation so recovery cannot pick a new default.
    dispatch_target = models.JSONField(default=dict, blank=True)
    # Container/job ID assigned by the Dispatch Service.
    external_id = models.CharField(max_length=255, blank=True, default="")
    # Push-mode: Controller POSTs result here on terminal transition.
    callback_url = models.URLField(blank=True, default="")
    # SHA-256 of the short-lived, task-scoped callback Bearer token injected
    # into the pod.  The plaintext is minted at spawn and never persisted.
    callback_token_hash = models.CharField(max_length=64, blank=True, default="")
    timeout_seconds = models.IntegerField(default=300)
    # Granted only after the native backend reserves a bounded input-wait deadline.
    input_wait_budget_seconds = models.PositiveIntegerField(default=0)
    # Terminal outputs — only one is populated depending on outcome.
    result = models.JSONField(null=True, blank=True)
    failure = models.JSONField(null=True, blank=True)
    event_sequence = models.PositiveIntegerField(default=0)
    event_bytes = models.PositiveIntegerField(default=0)
    # Per-dispatch ad-hoc input frozen at task creation (#930). Carries the
    # ``runAstroliftAgent`` ad-hoc ``trigger_payload`` and the trigger-bound
    # dispatch's ``input_mapping``-shaped webhook payload. Surfaced to the
    # running agent container as the ``ASTROLIFT_TRIGGER_PAYLOAD`` env var
    # (JSON-encoded) by the K8s Job spawner. ``None`` for unattended
    # manual/cron/loop dispatch, which carry no ad-hoc input — those launch
    # with no such env var.
    dispatch_input = models.JSONField(null=True, blank=True)
    # Lifecycle timestamps.
    queued_at = models.DateTimeField(null=True, blank=True)
    provisioning_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    # Durable operator intent; settlement waits for confirmed container deletion.
    cancel_requested_at = models.DateTimeField(null=True, blank=True)
    # Redis key for live status/heartbeat/progress/token data.
    telemetry_key = models.CharField(max_length=512, blank=True, default="")
    # Environment recipe this task launched from.  Nullable so a task
    # survives spec deletion (the resolved values are frozen elsewhere).
    environment_spec = models.ForeignKey(
        "astrolift_agents.AgentEnvironmentSpec",
        related_name="agent_tasks",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # Pod/container name assigned by the dispatcher backend.
    pod_name = models.CharField(max_length=255, blank=True, default="")
    # The Zentinelle agent whose key a model-gateway run holds (#1851), so
    # stop and finish revoke it; its usage is metered under this id. Empty
    # for a run without the gateway.
    model_gateway_agent_id = models.CharField(max_length=100, blank=True, default="")
    # The connection whose install minted that key: the only one Zentinelle
    # lets renew or revoke it, even after the organization reconnects.
    model_gateway_connection = models.ForeignKey(
        "astrolift_operations.ZentinelleConnection",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # K8s namespace the dispatcher actually spawned the Job in, frozen at
    # spawn. The log resolver reads this back rather than recomputing the
    # namespace — different dispatch paths land in different namespaces, so
    # a recomputed guess can miss the pod (#891). Blank on pre-#891 rows;
    # the resolver falls back to the per-org agent namespace for those.
    namespace = models.CharField(max_length=255, blank=True, default="")
    # "owner/repo@sha" frozen at dispatch so the run is reproducible.
    source_ref = models.CharField(max_length=512, blank=True, default="")
    # Frozen at spawn from the env spec's ``vnc_enabled`` so the task is
    # self-describing even after the spec is edited/deleted. Drives the
    # -vnc image variant + containerPort 5900 (raw RFB) in the spawner.
    vnc_enabled = models.BooleanField(default=False)
    # Relay path to the live RFB framebuffer, set when the task reaches
    # RUNNING on a VNC-capable pod. Empty otherwise. Matches the ASGI
    # relay registered at ``/app/vnc/<guid>`` (see core.schema.vnc_ws).
    vnc_url = models.CharField(max_length=512, blank=True, default="")
    # Blob key the pod-side snapshot uploader PUTs framebuffer JPEGs to,
    # frozen at spawn for VNC tasks (``snapshots/<guid>/latest.jpg``). Empty
    # for non-VNC tasks. The gallery resolves this to a presigned GET URL.
    snapshot_key = models.CharField(max_length=512, blank=True, default="")

    # Statuses that mean the task has settled — no further work happens and
    # it no longer counts as "in flight". The complement (NON_TERMINAL_*)
    # is what the PR-6 Loop controller counts toward an agent's concurrency
    # cap. Kept in sync with the terminal nodes of ``_TRANSITIONS`` (the
    # states whose allowed-transition set is empty) and with the dispatch
    # activity's own terminal set (``agent_stage._TERMINAL_STATUSES``).
    TERMINAL_STATUSES: frozenset[str] = frozenset(
        {
            Status.COMPLETED,
            Status.FAILED,
            Status.TIMED_OUT,
            Status.CANCELLED,
        }
    )
    NON_TERMINAL_STATUSES: frozenset[str] = frozenset(
        {
            Status.DRAFT,
            Status.QUEUED,
            Status.PROVISIONING,
            Status.RUNNING,
        }
    )

    class Meta:
        indexes = [
            models.Index(
                fields=["organization", "status"],
                name="agent_task_org_status_idx",
            ),
            # PR-6: the Loop controller counts non-terminal tasks per agent;
            # index (agent_definition, status) so that count is cheap.
            models.Index(
                fields=["agent_definition", "status"],
                name="agent_task_agentdef_status_idx",
            ),
        ]

    # Allowed forward transitions.  Terminal states map to empty sets.
    _TRANSITIONS: dict[str, set[str]] = {
        # Preparation and durable-workflow enqueue can fail before a container
        # exists; those are real terminal failures, not immortal draft/queued
        # rows. Cancellation remains legal in both pre-spawn states.
        Status.DRAFT: {Status.QUEUED, Status.CANCELLED, Status.FAILED},
        Status.QUEUED: {Status.PROVISIONING, Status.CANCELLED, Status.FAILED, Status.TIMED_OUT},
        Status.PROVISIONING: {
            Status.RUNNING,
            Status.CANCELLED,
            Status.FAILED,
            Status.TIMED_OUT,
        },
        Status.RUNNING: {
            Status.COMPLETED,
            Status.FAILED,
            Status.TIMED_OUT,
            Status.CANCELLED,
        },
        Status.COMPLETED: set(),
        Status.FAILED: set(),
        Status.TIMED_OUT: set(),
        Status.CANCELLED: set(),
    }

    def clean(self) -> None:
        """Validate that the current status is a reachable state.

        Called by Django's full_clean(); raises ValidationError on an
        invalid transition.  Direct field assignment is not blocked here
        (use ``transition_to`` for that guard).
        """
        if self.pk is not None:
            try:
                current_db = AgentTask.all_objects.filter(pk=self.pk).values_list("status", flat=True).first()
            except Exception:
                current_db = None

            if current_db is not None and current_db != self.status:
                allowed = self._TRANSITIONS.get(current_db, set())
                if self.status not in allowed:
                    raise ValidationError(
                        f"AgentTask({self.pk}) cannot transition {current_db!r} → {self.status!r}"
                    )

    def transition_to(self, new_status: str) -> None:
        """Advance to ``new_status``, stamping timing fields.

        Raises ``ValueError`` for invalid transitions so callers can
        surface the reason without catching a broad exception class.
        """
        with transaction.atomic():
            task = type(self).all_objects.select_for_update().get(pk=self.pk)
            current = task.status
            allowed = self._TRANSITIONS.get(current, set())
            if task.cancel_requested_at and new_status != self.Status.CANCELLED:
                raise ValueError(f"AgentTask({self.pk}) is awaiting cancellation")
            if new_status not in allowed:
                raise ValueError(f"AgentTask({self.pk}) cannot transition {current!r} → {new_status!r}")
            now = timezone.now()
            task.status = new_status
            if new_status == self.Status.QUEUED:
                task.queued_at = task.queued_at or now
            elif new_status == self.Status.PROVISIONING:
                task.provisioning_at = task.provisioning_at or now
            elif new_status == self.Status.RUNNING:
                task.started_at = task.started_at or now
                if task.vnc_enabled and not task.vnc_url:
                    task.vnc_url = f"/app/vnc/{task.guid}"
            elif new_status in {
                self.Status.COMPLETED,
                self.Status.FAILED,
                self.Status.TIMED_OUT,
                self.Status.CANCELLED,
            }:
                task.ended_at = task.ended_at or now
                task.callback_token_hash = ""

            fields = [
                "status",
                "queued_at",
                "provisioning_at",
                "started_at",
                "ended_at",
                "vnc_url",
                "callback_token_hash",
                "updated_at",
                "version",
            ]
            task.save(update_fields=fields)
            for field in fields:
                setattr(self, field, getattr(task, field))
        try:
            from astrolift_operations.zentinelle_bridge import emit_agent_task_transition

            emit_agent_task_transition(self, new_status)
        except Exception:  # noqa: BLE001 - evidence must never block a task
            pass

    def __str__(self) -> str:
        return f"AgentTask {self.guid} ({self.status})"


def resolve_agent_task_for_run(run) -> AgentTask | None:
    """Resolve the :class:`AgentTask` a fleet-history ``AgentRun`` dispatched.

    FK-first, fuzzy-fallback (#1217): returns the task explicitly linked via
    the ``AgentTask.agent_run`` FK when set, otherwise falls back to the
    historical ``(workload, external_id == k8s_pod_name)`` join the platform's
    reconciler and dispatch bridge have always used. This keeps every existing
    caller working while the FK backfills — rows dispatched before the FK
    landed (or push-mode / pre-spawn runs) resolve through the fuzzy join
    until the next reconcile stamps the FK.

    Returns ``None`` when no task is linked yet (push-mode, pre-spawn, a run
    with no AgentTask, or a run with no pod name) so callers skip rather than
    mis-attribute. Read-only: it never writes the FK — population happens at
    the sanctioned dispatch / reconcile points.
    """
    if run is None:
        return None
    linked = AgentTask.objects.filter(agent_run_id=run.pk, deleted_at__isnull=True).first()
    if linked is not None:
        return linked
    pod_name = getattr(run, "k8s_pod_name", "")
    if not pod_name:
        return None
    return (
        AgentTask.objects.filter(
            agent_definition_id=run.workload_id,
            external_id=pod_name,
            deleted_at__isnull=True,
        )
        .exclude(external_id="")
        .order_by("-created_at")
        .first()
    )
