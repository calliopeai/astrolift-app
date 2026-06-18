"""
AgentTask — the discrete unit of agent dispatch.

Ephemeral, org-scoped, with a lifecycle state machine covering the full
dispatch path from creation to terminal outcome.

State machine (issue #44):

    DRAFT → QUEUED: explicit enqueue mutation (or auto on Brief READY)
    QUEUED → PROVISIONING: dispatcher selected + spawn request sent
    PROVISIONING → RUNNING: Dispatch Service confirms container started
    RUNNING → COMPLETED | FAILED | TIMED_OUT | CANCELLED: terminal
    CANCELLED: only from DRAFT | QUEUED | PROVISIONING
      (RUNNING requires a stop signal to the Dispatcher)

The allowed graph is encoded in ``_TRANSITIONS`` — ``transition_to`` is
the only sanctioned way to advance status.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
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
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    # Container/job ID assigned by the Dispatch Service.
    external_id = models.CharField(max_length=255, blank=True, default="")
    # Push-mode: Controller POSTs result here on terminal transition.
    callback_url = models.URLField(blank=True, default="")
    timeout_seconds = models.IntegerField(default=300)
    # Terminal outputs — only one is populated depending on outcome.
    result = models.JSONField(null=True, blank=True)
    failure = models.JSONField(null=True, blank=True)
    # Lifecycle timestamps.
    queued_at = models.DateTimeField(null=True, blank=True)
    provisioning_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
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
        Status.DRAFT: {Status.QUEUED, Status.CANCELLED},
        Status.QUEUED: {Status.PROVISIONING, Status.CANCELLED},
        Status.PROVISIONING: {Status.RUNNING, Status.CANCELLED, Status.FAILED},
        Status.RUNNING: {Status.COMPLETED, Status.FAILED, Status.TIMED_OUT},
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
                        f"AgentTask({self.pk}) cannot transition " f"{current_db!r} → {self.status!r}"
                    )

    def transition_to(self, new_status: str) -> None:
        """Advance to ``new_status``, stamping timing fields.

        Raises ``ValueError`` for invalid transitions so callers can
        surface the reason without catching a broad exception class.
        """
        current = self.status
        allowed = self._TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise ValueError(f"AgentTask({self.pk}) cannot transition {current!r} → {new_status!r}")
        now = timezone.now()
        self.status = new_status
        if new_status == self.Status.QUEUED:
            self.queued_at = self.queued_at or now
        elif new_status == self.Status.PROVISIONING:
            self.provisioning_at = self.provisioning_at or now
        elif new_status == self.Status.RUNNING:
            self.started_at = self.started_at or now
            # The framebuffer only exists once the pod is RUNNING; publish
            # the relay path now so the GraphQL read surface can expose it.
            if self.vnc_enabled and not self.vnc_url:
                self.vnc_url = f"/app/vnc/{self.guid}"
        elif new_status in {
            self.Status.COMPLETED,
            self.Status.FAILED,
            self.Status.TIMED_OUT,
            self.Status.CANCELLED,
        }:
            self.ended_at = self.ended_at or now

        self.save(
            update_fields=[
                "status",
                "queued_at",
                "provisioning_at",
                "started_at",
                "ended_at",
                "vnc_url",
                "updated_at",
                "version",
            ]
        )

    def __str__(self) -> str:
        return f"AgentTask {self.guid} ({self.status})"
