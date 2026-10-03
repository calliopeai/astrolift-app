"""Registered-dispatch execution fencing and final failure settlement.

Only metadata from the actual Temporal start event is retained. This is not
physical-container attestation and does not adopt a different workflow run.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from asgiref.sync import sync_to_async
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import ApplicationError

from astrolift_workflows.inputs import DispatchAgentTaskInput

_SAFE_MESSAGE = "Agent dispatch could not finish."


async def execution_for_activity(task_pk: int) -> dict[str, Any]:
    """Bind the worker's run to its original input, never a caller-supplied run."""
    info = activity.info()
    client = activity.client()
    if info.workflow_type != "DispatchAgentTaskWorkflow" or not info.workflow_run_id or not info.workflow_id:
        raise ApplicationError("Agent dispatch execution is not eligible.", non_retryable=True)
    current = await client.get_workflow_handle(info.workflow_id).describe(rpc_timeout=timedelta(seconds=10))
    if current.run_id != info.workflow_run_id or current.status != WorkflowExecutionStatus.RUNNING:
        raise ApplicationError("Agent dispatch execution is no longer current.", non_retryable=True)
    handle = client.get_workflow_handle(info.workflow_id, run_id=info.workflow_run_id)
    event = await handle.fetch_history_events(page_size=1, rpc_timeout=timedelta(seconds=10)).__anext__()
    start = event.workflow_execution_started_event_attributes
    values = await client.data_converter.decode(start.input.payloads, [DispatchAgentTaskInput])
    if not values or values[0].agent_task_id != task_pk:
        raise ApplicationError("Agent dispatch input does not match its task.", non_retryable=True)
    actor = values[0].actor
    return {
        "namespace": info.namespace,
        "workflow_id": info.workflow_id,
        "run_id": info.workflow_run_id,
        "actor": {"kind": actor.kind, "user_id": actor.user_id, "token_id": actor.token_id},
        "activity_id": info.activity_id,
        "activity_attempt": info.attempt,
    }


def bind_execution(task, execution: dict[str, Any], *, finalizing: bool = False) -> None:
    """Caller holds the task control lock; the row lock fences callbacks/writes."""
    from django.db import transaction

    from astrolift_agents.models import AgentTask

    with transaction.atomic():
        current = AgentTask.all_objects.select_for_update().get(pk=task.pk)
        expected_id = f"DispatchAgentTaskWorkflow-{current.guid}"
        if execution["workflow_id"] != expected_id:
            raise ApplicationError("Agent dispatch workflow does not match its task.", non_retryable=True)
        binding = current.dispatch_execution
        identity = {key: execution[key] for key in ("namespace", "workflow_id", "run_id", "actor")}
        if binding and any(binding.get(key) != value for key, value in identity.items()):
            raise ApplicationError("Agent dispatch execution has changed.", non_retryable=True)
        if binding.get("final_reason") and not finalizing:
            raise ApplicationError("Agent dispatch is being finalized.", non_retryable=True)
        if current.status in AgentTask.TERMINAL_STATUSES:
            return
        if not finalizing:
            binding = {
                **binding,
                **identity,
                "dispatch_activity_id": execution["activity_id"],
                "dispatch_activity_attempt": max(
                    binding.get("dispatch_activity_attempt", 0), execution["activity_attempt"]
                ),
            }
        else:
            binding = {**binding, **identity}
        if current.dispatch_execution == binding:
            return
        current.dispatch_execution = binding
        current.save(update_fields=["dispatch_execution", "updated_at", "version"])


def finalize_dispatch_sync(task_pk: int, execution: dict[str, Any], reason: str) -> dict[str, Any]:
    """Stop only frozen placement, then use the existing locked terminal primitive.

    An ambiguous stop keeps the task and outbox nonterminal. Activity retries
    resume the persisted intent; a completed/cancelled task always wins.
    """
    from django.db import transaction

    from astrolift_agents.models import AgentTask
    from astrolift_agents.services.task_target import spawner_for_task, task_control_lock

    if reason not in {"failed", "timed_out", "cancelled"}:
        raise ApplicationError("Agent dispatch failure classification is invalid.", non_retryable=True)
    with task_control_lock(task_pk):
        task = AgentTask.all_objects.get(pk=task_pk)
        bind_execution(task, execution, finalizing=True)
        with transaction.atomic():
            task = AgentTask.all_objects.select_for_update().get(pk=task_pk)
            if task.status in AgentTask.TERMINAL_STATUSES:
                return {"task_guid": str(task.guid), "status": task.status}
            binding = task.dispatch_execution
            if binding.get("final_reason") not in (None, reason):
                raise ApplicationError("Agent dispatch finalization has changed.", non_retryable=True)
            task.dispatch_execution = {
                **binding,
                "final_reason": reason,
                "final_activity_id": execution["activity_id"],
            }
            task.save(update_fields=["dispatch_execution", "updated_at", "version"])
            external_id = task.external_id or task.dispatch_target.get("planned_external_id", "")
            unproven_effect = not external_id and task.status not in {
                AgentTask.Status.DRAFT,
                AgentTask.Status.QUEUED,
            }
        if unproven_effect:
            raise ApplicationError("Agent dispatch cleanup is not yet confirmed.")
        if external_id:
            # No fallback to an org's current default cluster, nor unowned Job adoption.
            if not task.dispatch_target:
                raise ApplicationError("Agent dispatch cleanup is not yet confirmed.")
            spawner = spawner_for_task(task)
            spawner.stop(external_id, expected_task_guid=str(task.guid))
            if not spawner.confirm_stopped(external_id):
                raise ApplicationError("Agent dispatch cleanup is not yet confirmed.")
        with transaction.atomic():
            task = AgentTask.all_objects.select_for_update().get(pk=task_pk)
            if task.status not in AgentTask.TERMINAL_STATUSES:
                status = AgentTask.Status.CANCELLED if task.cancel_requested_at else reason
                if status != AgentTask.Status.CANCELLED:
                    task.failure = {
                        "code": "AGENT_DISPATCH_TIMEOUT"
                        if reason == "timed_out"
                        else "AGENT_DISPATCH_EXHAUSTED",
                        "message": _SAFE_MESSAGE,
                    }
                    task.save(update_fields=["failure", "updated_at", "version"])
                task.transition_to(status)
            return {"task_guid": str(task.guid), "status": task.status}


@activity.defn(name="astrolift.agent.finalize_dispatch")
async def finalize_agent_dispatch(task_pk: int, reason: str) -> dict[str, Any]:
    try:
        execution = await execution_for_activity(task_pk)
        return await sync_to_async(finalize_dispatch_sync, thread_sensitive=False)(task_pk, execution, reason)
    except ApplicationError:
        raise
    except Exception:  # noqa: BLE001 — retry without copying provider/DB bodies to history/logs
        raise ApplicationError("Agent dispatch cleanup is not yet confirmed.") from None
