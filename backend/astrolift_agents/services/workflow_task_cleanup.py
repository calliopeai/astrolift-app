"""Stop only tasks explicitly owned by a closed workflow, with durable retries."""

from __future__ import annotations


def run_owner(run) -> dict:
    return {
        "pk": run.pk,
        "organization_id": run.organization_id,
        "workflow_id": run.workflow_id,
        "run_id": run.run_id,
    }


def validate_task_owner(task, owner: dict) -> str:
    from astrolift_lifecycle.models import AgentRun
    from astrolift_operations.models import WorkflowRun
    from workflows.models import WorkflowStageExecution

    run = WorkflowRun.objects.filter(**owner).first()
    if run is None or run.status == "running" or run.ended_at is None:
        return "Workflow ownership changed or execution is still running"
    if task.organization_id != owner["organization_id"] or task.agent_run_id is None:
        return "Task belongs to another organization or has no explicit agent run"
    agent_run = (
        AgentRun.objects.select_related("workload__registered_app").filter(pk=task.agent_run_id).first()
    )
    if agent_run is None or (
        task.agent_definition_id != agent_run.workload_id
        or agent_run.workload.registered_app.organization_id != owner["organization_id"]
    ):
        return "Task workload ownership does not match this workflow"
    links = WorkflowStageExecution.objects.filter(agent_run_id=task.agent_run_id, deleted_at__isnull=True)
    if not links.filter(workflow_run_id=run.pk).exists() or links.exclude(workflow_run_id=run.pk).exists():
        return "Agent run is not exclusively owned by this workflow"
    return ""


def unfinished_tasks(run_pk: int):
    from django.db.models import Q

    from astrolift_agents.models import AgentTask

    return (
        AgentTask.objects.filter(
            agent_run__stage_executions__workflow_run_id=run_pk,
            agent_run__stage_executions__deleted_at__isnull=True,
        )
        .exclude(status=AgentTask.Status.COMPLETED)
        .filter(Q(failure__task_cleanup__status__isnull=True) | ~Q(failure__task_cleanup__status="completed"))
        .distinct()
    )


def cleanup_workflow_tasks(run_pk: int, *, limit: int = 5, expected_owner: dict | None = None) -> bool:
    from django.db import transaction

    from astrolift_operations.models import WorkflowRun
    from astrolift_workflows.activities.agent_stage import _cancel_agent_task_sync

    run = WorkflowRun.objects.filter(pk=run_pk).first()
    if run is None or run.status == "running" or run.ended_at is None:
        return False
    owner = run_owner(run)
    if expected_owner is not None and expected_owner != owner:
        return False
    tasks = unfinished_tasks(run.pk)
    previous = (run.failure or {}).get("task_cleanup", {})
    cursor = int(previous.get("cursor", 0))
    batch = list(tasks.filter(pk__gt=cursor).order_by("pk")[:limit])
    if not batch and cursor:
        batch = list(tasks.order_by("pk")[:limit])
    if not batch and not previous:
        return False
    errors = {row["task_guid"]: row for row in previous.get("errors", [])}
    for task in batch:
        task_guid = str(task.guid)
        try:
            result = _cancel_agent_task_sync(task_guid, owner=owner)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        if result["ok"]:
            errors.pop(task_guid, None)
        else:
            errors[task_guid] = {
                "task_guid": task_guid,
                "message": result.get("error", "Task cleanup failed"),
                "pending": bool(result.get("pending")),
            }
    remaining = unfinished_tasks(run.pk).count()
    if not remaining:
        errors = {}
    packet = {
        "status": "completed"
        if remaining == 0
        else ("failed" if any(not row["pending"] for row in errors.values()) else "pending"),
        "remaining": remaining,
        "errors": list(errors.values())[-20:],
        "cursor": batch[-1].pk if batch else cursor,
    }
    with transaction.atomic():
        current = WorkflowRun.objects.select_for_update().filter(**owner).first()
        if current is None or current.status == "running" or current.ended_at is None:
            return False
        if (current.failure or {}).get("task_cleanup") == packet:
            return False
        current.failure = {**(current.failure or {}), "task_cleanup": packet}
        current.save(update_fields=["failure", "updated_at", "version"])
    return True
