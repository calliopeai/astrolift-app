"""
AgentInteraction — append-only log of control-plane-observed agent activity.

One row per interaction the control plane can see for a given
:class:`~astrolift_agents.models.agent_task.AgentTask`: an authenticated
agent->controller dispatch call, a platform/control-plane tool call, a
signal, or a gate stage. It is the queryable, per-agent-task telemetry
surface behind the LiveFlowMap P3 interaction map (#1216 / #1092).

V1 captures control-plane-VISIBLE interactions only. Internal LLM
tool-uses inside the agent pod (bash/edit/etc.) are deliberately out of
scope — capturing those needs cross-repo agent-runtime work and straddles
the Zentinelle boundary (see #1216).

Append-only at the application layer (``AppendOnlyMixin``), mirroring
:class:`~astrolift_operations.models.event.Event` and
:class:`~astrolift_operations.models.audit_event.AuditEvent`: rows are
created by capture points and never updated or deleted.
"""

from __future__ import annotations

import logging

from django.db import models
from django.utils import timezone

from core.fields import UUIDv7Field
from core.mixins import AppendOnlyMixin

logger = logging.getLogger(__name__)


class AgentInteraction(AppendOnlyMixin, models.Model):
    class Kind(models.TextChoices):
        # An authenticated agent->controller Control API call
        # (checkin/callback/status/meter/logs).
        CONTROL_API = "control_api"
        # A platform/control-plane tool invocation observed by the control
        # plane (reserved for the tool-boundary capture surface).
        TOOL_CALL = "tool_call"
        # A workflow signal delivered to / emitted for the task.
        SIGNAL = "signal"
        # A human-gate stage (notify / decision).
        GATE = "gate"

    guid = UUIDv7Field(unique=True, db_index=True)
    # REQUIRED org scope: every read of this surface filters on it
    # fail-closed, so it must always be populated (never nullable).
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="agent_interactions",
        on_delete=models.CASCADE,
        db_index=True,
    )
    agent_task = models.ForeignKey(
        "astrolift_agents.AgentTask",
        related_name="interactions",
        on_delete=models.CASCADE,
        db_index=True,
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    # Stable label for the interaction: endpoint path / tool / signal /
    # gate stage name (e.g. "callback", "status", "checkin").
    name = models.CharField(max_length=255)
    # Coarse outcome — ok / error / pending / approved / rejected.
    status = models.CharField(max_length=32, default="ok")
    # Set by the writer (capture points pass it or default to now via
    # ``record_interaction``) — NOT auto_now_add, so a capture point can
    # stamp the moment the interaction actually occurred.
    occurred_at = models.DateTimeField(db_index=True)
    # Small, bounded structured payload (http method, status code,
    # duration_ms, new_status, ...). Keep it compact.
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["agent_task", "occurred_at"],
                name="agentixn_task_time_idx",
            ),
            models.Index(
                fields=["organization", "-occurred_at"],
                name="agentixn_org_time_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"AgentInteraction {self.kind}:{self.name} ({self.status})"


def record_interaction(
    agent_task,
    *,
    kind: str,
    name: str,
    status: str = "ok",
    detail: dict | None = None,
    occurred_at=None,
) -> AgentInteraction | None:
    """Append one :class:`AgentInteraction` for ``agent_task``.

    Defensive by contract: capture is additive and side-effect-only, so a
    write failure here must NEVER break the caller (an agent callback, a
    dispatch report, ...). Any exception is logged and swallowed, and the
    function returns ``None`` instead of raising.

    The organization is taken from ``agent_task`` so the row is always
    tenant-scoped. ``occurred_at`` defaults to now when not supplied.
    """
    try:
        return AgentInteraction.objects.create(
            organization_id=agent_task.organization_id,
            agent_task=agent_task,
            kind=kind,
            name=name,
            status=status,
            detail=detail or {},
            occurred_at=occurred_at or timezone.now(),
        )
    except Exception:  # noqa: BLE001 — capture must never break the caller
        logger.exception(
            "failed to record agent interaction (kind=%s name=%s) for task %s",
            kind,
            name,
            getattr(agent_task, "pk", None),
        )
        return None
