"""Pipeline and job run state machine — valid transitions and audit events (#87).

Defines the legal state transitions for PipelineRun and JobRun, enforces
them at the model level, and emits audit events on every transition.

State machine for PipelineRun:
    PENDING → RUNNING → SUCCESS | FAILURE | CANCELLED

State machine for JobRun:
    PENDING → RUNNING → SUCCESS | FAILURE | SKIPPED | CANCELLED

The state machine is intentionally append-only: once a terminal state
is reached (SUCCESS, FAILURE, SKIPPED, CANCELLED) no further transitions
are allowed. This mirrors the Temporal workflow lifecycle.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class InvalidTransition(ValueError):
    """Raised when a state transition is not legal."""


# Valid transition maps: {current_state: {allowed_next_states}}
_PIPELINE_RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"running", "cancelled"}),
    "running": frozenset({"success", "failure", "cancelled"}),
    # Terminal states — no further transitions allowed
    "success": frozenset(),
    "failure": frozenset(),
    "cancelled": frozenset(),
}

_JOB_RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"running", "skipped", "cancelled"}),
    "running": frozenset({"success", "failure", "cancelled"}),
    # Terminal states
    "success": frozenset(),
    "failure": frozenset(),
    "skipped": frozenset(),
    "cancelled": frozenset(),
}


def validate_pipeline_run_transition(current: str, next_state: str) -> None:
    """Raise InvalidTransition if the transition is not legal."""
    allowed = _PIPELINE_RUN_TRANSITIONS.get(current, frozenset())
    if next_state not in allowed:
        raise InvalidTransition(
            f"PipelineRun cannot transition from {current!r} to {next_state!r}. "
            f"Allowed: {sorted(allowed) or ['(terminal)']}"
        )


def validate_job_run_transition(current: str, next_state: str) -> None:
    """Raise InvalidTransition if the transition is not legal."""
    allowed = _JOB_RUN_TRANSITIONS.get(current, frozenset())
    if next_state not in allowed:
        raise InvalidTransition(
            f"JobRun cannot transition from {current!r} to {next_state!r}. "
            f"Allowed: {sorted(allowed) or ['(terminal)']}"
        )


def transition_pipeline_run(run, next_status: str, *, actor_display: str = "system") -> None:
    """Transition a PipelineRun to the next status with validation and audit event.

    Updates started_at / finished_at timestamps based on the transition.
    Saves only the changed fields to avoid clobbering concurrent updates.
    """
    from django.utils import timezone

    validate_pipeline_run_transition(run.status, next_status)

    now = timezone.now()
    update_fields = ["status", "updated_at", "version"]

    run.status = next_status

    if next_status == "running" and run.started_at is None:
        run.started_at = now
        update_fields.append("started_at")

    if next_status in {"success", "failure", "cancelled"} and run.finished_at is None:
        run.finished_at = now
        update_fields.append("finished_at")

    run.save(update_fields=update_fields)

    _emit_pipeline_run_event(run, next_status, actor_display=actor_display)
    logger.info(
        "pipelines.state_machine: PipelineRun %s → %s (run=%s)",
        run.status,
        next_status,
        run.guid,
    )


def transition_job_run(job_run, next_status: str, *, actor_display: str = "system") -> None:
    """Transition a JobRun to the next status with validation and audit event."""
    from django.utils import timezone

    validate_job_run_transition(job_run.status, next_status)

    now = timezone.now()
    update_fields = ["status", "updated_at", "version"]

    job_run.status = next_status

    if next_status == "running" and job_run.started_at is None:
        job_run.started_at = now
        update_fields.append("started_at")

    if next_status in {"success", "failure", "skipped", "cancelled"} and job_run.finished_at is None:
        job_run.finished_at = now
        update_fields.append("finished_at")

    job_run.save(update_fields=update_fields)

    _emit_job_run_event(job_run, next_status, actor_display=actor_display)


def _emit_pipeline_run_event(run, status: str, *, actor_display: str) -> None:
    """Emit a core audit event for a pipeline run status transition."""
    try:
        from core.events import emit_event

        emit_event(
            action=f"pipeline_run.{status}",
            resource_kind="pipeline_run",
            resource_id=str(run.guid),
            actor_display=actor_display,
            org_id=run.pipeline.organization_id,
            metadata={
                "pipeline_name": run.pipeline.name,
                "run_number": run.run_number,
                "trigger_kind": run.trigger_kind,
            },
        )
    except Exception:  # noqa: BLE001
        pass  # Events are best-effort; don't break the state transition


def _emit_job_run_event(job_run, status: str, *, actor_display: str) -> None:
    """Emit a core audit event for a job run status transition."""
    try:
        from core.events import emit_event

        emit_event(
            action=f"pipeline_job_run.{status}",
            resource_kind="pipeline_job_run",
            resource_id=str(job_run.guid),
            actor_display=actor_display,
            org_id=job_run.pipeline_run.pipeline.organization_id,
            metadata={
                "job_id": job_run.job.job_id if job_run.job_id else "",
                "run_number": job_run.pipeline_run.run_number,
            },
        )
    except Exception:  # noqa: BLE001
        pass
