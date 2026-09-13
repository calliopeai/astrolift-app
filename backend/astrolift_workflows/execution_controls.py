"""Exact, tenant-owned definition execution observation and control."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from astrolift_workflows.activities.workflow_run_reconcile import RunIdentity, _apply_observation
from astrolift_workflows.client import cancel_workflow, describe_workflow_instance, terminate_workflow

WORKFLOW_KIND = "WorkflowDefinitionRunWorkflow"
STATUSES = {
    "RUNNING": "running",
    "COMPLETED": "completed",
    "FAILED": "failed",
    "CANCELED": "cancelled",
    "TERMINATED": "terminated",
    "TIMED_OUT": "timed_out",
}
TERMINAL = set(STATUSES.values()) - {"running"}


def execution_lookup(identifier: str) -> dict | None:
    text = str(identifier)
    if text.isascii() and text.isdecimal() and len(text) <= 19:
        number = int(text)
        return {"pk": number} if 0 < number < 1 << 63 else None
    try:
        return {"guid": UUID(text)}
    except (ValueError, TypeError, AttributeError):
        return None


def find_execution(organization_id: int | None, identifier: str):
    from astrolift_operations.models import WorkflowRun

    lookup = execution_lookup(identifier)
    if organization_id is None or lookup is None:
        return None
    return (
        WorkflowRun.objects.filter(
            **lookup,
            organization_id=organization_id,
            organization__deleted_at__isnull=True,
            workflow_kind=WORKFLOW_KIND,
            deleted_at__isnull=True,
        )
        .select_related("organization", "workflow_definition")
        .first()
    )


def observe_execution(run) -> tuple[dict | None, str]:
    if not run.workflow_id or not run.run_id:
        return None, "This execution has no complete Temporal identity yet"
    try:
        observed = describe_workflow_instance(run.workflow_id, run_id=run.run_id)
    except Exception:
        observed = None
    if observed is None:
        return None, "Temporal is unavailable; the last recorded execution state is retained"
    if (observed.get("workflow_id"), observed.get("run_id"), observed.get("workflow_type")) != (
        run.workflow_id,
        run.run_id,
        WORKFLOW_KIND,
    ):
        return None, "Temporal returned a different execution; the last recorded state is retained"
    status = STATUSES.get(observed.get("status"))
    if status is None:
        return None, "Temporal returned an unsupported execution status"
    closed_at = None
    if status in TERMINAL:
        try:
            closed_at = datetime.fromisoformat(observed.get("closed_at") or "")
            if closed_at.utcoffset() is None:
                raise ValueError("missing timezone")
        except (TypeError, ValueError):
            return None, "Temporal has not confirmed this execution's close time"
    return {"status": status, "closed_at": closed_at}, ""


def execution_state(run, *, observation: tuple[dict | None, str] | None = None) -> dict:
    from astrolift_agents.services.workflow_task_cleanup import unfinished_tasks

    observed, error = observation if observation is not None else observe_execution(run)
    status = observed["status"] if observed else run.status
    ended_at = observed["closed_at"] if observed else run.ended_at
    terminal = status in TERMINAL
    remaining = unfinished_tasks(run.pk).count()
    failure = run.failure if isinstance(run.failure, dict) else {}
    packet = failure.get("task_cleanup") or {}
    if not isinstance(packet, dict):
        packet = {}
    if not terminal:
        cleanup_status = "not_requested"
    elif not remaining:
        cleanup_status = "completed" if packet.get("status") == "completed" else "not_required"
    else:
        cleanup_status = "failed" if packet.get("status") == "failed" else "pending"
    return {
        "guid": str(run.guid),
        "record_id": str(run.pk),
        "organization_guid": str(run.organization.guid),
        "definition_slug": (run.workflow_definition.slug or "") if run.workflow_definition_id else "",
        "status": status,
        "temporal_workflow_id": run.workflow_id,
        "temporal_run_id": run.run_id or None,
        "started_at": run.started_at,
        "ended_at": ended_at,
        "is_terminal": terminal,
        "failure": run.failure,
        "task_cleanup": {
            "status": cleanup_status,
            "remaining": remaining,
            "errors": packet.get("errors", []) if remaining else [],
            "retryable": bool(terminal and remaining and not error),
        },
        "observation_error": error,
    }


def control_execution(run, *, workflow_id: str, run_id: str, action: str, reason: str = "") -> dict:
    if not workflow_id or not run_id or (run.workflow_id, run.run_id) != (workflow_id, run_id):
        raise ValueError("The requested Temporal execution does not match this record")
    if action not in {"cancel", "terminate", "cleanup"}:
        raise ValueError("Action must be cancel, terminate, or cleanup")
    reason = reason.strip()
    if action == "terminate" and not reason:
        raise ValueError("A reason is required for termination")
    if action != "terminate" and reason:
        raise ValueError("A reason applies only to termination")
    observation = observe_execution(run)
    observed, error = observation
    if observed is None:
        raise ValueError(error)
    if action == "cleanup":
        if observed["status"] not in TERMINAL:
            raise ValueError("The execution is still running; resource cleanup was not started")
        identity = RunIdentity(run.pk, run.organization_id, run.workflow_id, run.run_id, run.version)
        if _apply_observation(identity, observed["status"], observed["closed_at"]) is None:
            raise ValueError("The execution record changed; refresh before retrying cleanup")
    elif observed["status"] not in TERMINAL:
        from django.db import DatabaseError, connection, transaction

        from astrolift_operations.models import WorkflowRun

        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '2s'")
                current = WorkflowRun.objects.select_for_update().filter(pk=run.pk).first()
                if current is None or (
                    current.organization_id,
                    current.workflow_id,
                    current.run_id,
                    current.version,
                ) != (run.organization_id, workflow_id, run_id, run.version):
                    raise ValueError("The execution record changed; refresh before requesting control")
                delivered = (
                    terminate_workflow(workflow_id, reason, run_id=run_id)
                    if action == "terminate"
                    else cancel_workflow(workflow_id, run_id=run_id)
                )
        except DatabaseError as exc:
            raise ValueError("The execution record is busy; refresh before retrying control") from exc
        if not delivered:
            raise ValueError(f"{action.capitalize()} could not be delivered to this exact execution")
        observation = None
    # An already-closed exact execution is an idempotent acknowledgement.
    # Neither it nor a delayed retry can select another Temporal incarnation.
    fresh = find_execution(run.organization_id, str(run.guid))
    if fresh is None or (fresh.workflow_id, fresh.run_id) != (workflow_id, run_id):
        raise ValueError("The execution record changed; verify the requested operation before retrying")
    return execution_state(fresh, observation=observation)
