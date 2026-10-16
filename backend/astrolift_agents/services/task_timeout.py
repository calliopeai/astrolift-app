"""Server-observed human wait time, independent of callback/reconciler restarts.

A task hosting a moved conversation (#1971) is paused until the IDE commits
the transfer: its own timeout starts at that commit, and the transfer's
admission deadline bounds it before. Deadlines counted from the spawn (the
Job's ``activeDeadlineSeconds``, its gateway key, its payload URL) add the
paused admission through ``admission_allowance_seconds``.
"""

import math

from django.db.models import Q
from django.utils import timezone

from astrolift_agents.models import AgentConversationTransfer, AgentTask, AgentTaskEvent

INPUT_WAIT_BUDGET_SECONDS = 24 * 60 * 60


def _transfer_clock(task):
    """``(committed_at, admission_deadline)`` of the moved conversation
    ``task`` hosts, or None for every other task."""
    if not isinstance(task, AgentTask) or task.pk is None:
        return None
    return (
        AgentConversationTransfer.all_objects.filter(agent_task_id=task.pk)
        .values_list("committed_at", "admission_deadline")
        .first()
    )


def _clock_start(task, transfer):
    """When the task's own timeout started counting; None while it has not."""
    if transfer is not None:
        return transfer[0]
    return task.provisioning_at or task.started_at or task.queued_at


def task_timeout_reason(task, *, now=None):
    now = now or timezone.now()
    transfer = _transfer_clock(task)
    if transfer is not None and transfer[0] is None:
        from astrolift_agents.services.conversation_transfer import EXPIRED_REASON

        return EXPIRED_REASON if now >= transfer[1] else None
    started = _clock_start(task, transfer)
    if started is None:
        return None
    timeout = max(1, int(task.timeout_seconds or 300))
    elapsed = max(0, (now - started).total_seconds())
    budget = task.input_wait_budget_seconds
    waiting = _input_wait_seconds(task, started, now) if budget else 0.0
    if budget and waiting >= budget:
        return f"agent exceeded its {budget}s input-wait budget"
    if elapsed - waiting >= timeout:
        return f"agent exceeded its {timeout}s timeout"
    return None


def admission_allowance_seconds(task, *, now=None) -> int:
    """Seconds a moved conversation's paused admission adds to the deadlines
    counted from the spawn.

    Before the commit, the time left until the admission deadline; after
    it, the time the admission took. Zero for every other task.
    """
    transfer = _transfer_clock(task)
    if transfer is None:
        return 0
    committed_at, deadline = transfer
    if committed_at is None:
        return max(0, math.ceil((deadline - (now or timezone.now())).total_seconds()))
    origin = task.provisioning_at or task.started_at or task.queued_at or committed_at
    return max(0, math.ceil((committed_at - origin).total_seconds()))


def task_input_wait_seconds(task, *, now=None):
    return _input_wait_seconds(task, _clock_start(task, _transfer_clock(task)), now or timezone.now())


def _input_wait_seconds(task, started, now):
    if started is None or not task.input_wait_budget_seconds:
        return 0.0
    waiting = 0.0
    # Only typed requests can pause execution. A reply resumes the clock
    # even if the runner never acknowledges delivery with input_resolved.
    rows = list(
        AgentTaskEvent.all_objects.filter(agent_task=task)
        .filter(Q(request__isnull=False) | Q(kind=AgentTaskEvent.Kind.INPUT_RESOLVED))
        .values("turn_id", "message_id", "kind", "request", "created_at", "input_reply__created_at")
    )
    resolved = {
        (row["turn_id"], row["message_id"]): row["created_at"]
        for row in rows
        if row["kind"] == AgentTaskEvent.Kind.INPUT_RESOLVED
    }
    intervals = []
    for row in rows:
        if row["request"] is None:
            continue
        end = min(
            now,
            row["input_reply__created_at"] or now,
            resolved.get((row["turn_id"], row["message_id"]), now),
        )
        begin = max(started, row["created_at"])
        if end > begin:
            intervals.append((begin, end))
    # Concurrent approvals share one wall clock; they must not multiply it.
    cursor = started
    for begin, end in sorted(intervals):
        if end > cursor:
            waiting += (end - max(begin, cursor)).total_seconds()
            cursor = end
    return waiting


def reserve_input_wait(task, prepared):
    """Called under the callback task lock, before accepting the first question."""
    if (
        task.status in task.TERMINAL_STATUSES
        or task.input_wait_budget_seconds
        or not any(row.request is not None for row in prepared[0])
    ):
        return
    # Externally managed dispatchers retain their own execution/deadline policy.
    if not task.dispatch_target:
        return
    from astrolift_agents.services.task_target import spawner_for_task

    reason = task_timeout_reason(task)
    if reason:
        raise RuntimeError(reason)
    if task.model_gateway_agent_id:
        _renew_gateway_key_for_input_wait(task)
    spawner_for_task(task).reserve_input_wait(task, INPUT_WAIT_BUDGET_SECONDS)
    # A slow cluster API call also spends execution time before the question
    # exists. Do not acknowledge a new request after that budget has elapsed.
    if reason := task_timeout_reason(task):
        raise RuntimeError(reason)
    task.input_wait_budget_seconds = INPUT_WAIT_BUDGET_SECONDS
    task.save(update_fields=["input_wait_budget_seconds", "updated_at", "version"])


def _renew_gateway_key_for_input_wait(task):
    """Let the task's gateway key outlast the wait a question reserves (#1851).

    Renewed before the Job's deadline moves, so a refused renewal, or one that
    Zentinelle's key lifetime cuts short, refuses the question like a failed
    reservation and leaves the deadline where it was; the runner retries. The
    key has to last as long as the task's own clock can still run it: its
    timeout and the wait budget, less what it has spent.
    """
    from astrolift_dispatch.model_gateway import ModelGatewayError, key_covers, renew_run_key, task_key_ttl

    expires_at, _lifetime_ends_at = renew_run_key(
        connection_id=task.model_gateway_connection_id,
        agent_id=task.model_gateway_agent_id,
        ttl_seconds=task_key_ttl(task) + INPUT_WAIT_BUDGET_SECONDS,
    )
    started = _clock_start(task, _transfer_clock(task))
    spent = max(0.0, (timezone.now() - started).total_seconds()) if started else 0.0
    left = max(1, int(task.timeout_seconds or 300)) + INPUT_WAIT_BUDGET_SECONDS - spent
    if not key_covers(expires_at, left):
        raise ModelGatewayError(
            f"Zentinelle caps gateway key {task.model_gateway_agent_id} at {expires_at.isoformat()}, before "
            f"the task's {INPUT_WAIT_BUDGET_SECONDS}s input wait could end, so the question was refused; "
            "raise ASTROLIFT_AGENT_KEY_MAX_LIFETIME_SECONDS in Zentinelle"
        )
