"""Server-observed human wait time, independent of callback/reconciler restarts."""

from django.db.models import Q
from django.utils import timezone

from astrolift_agents.models import AgentTaskEvent

INPUT_WAIT_BUDGET_SECONDS = 24 * 60 * 60


def task_timeout_reason(task, *, now=None):
    started = task.provisioning_at or task.started_at or task.queued_at
    if started is None:
        return None
    now = now or timezone.now()
    timeout = max(1, int(task.timeout_seconds or 300))
    elapsed = max(0, (now - started).total_seconds())
    budget = task.input_wait_budget_seconds
    waiting = task_input_wait_seconds(task, now=now) if budget else 0.0
    if budget and waiting >= budget:
        return f"agent exceeded its {budget}s input-wait budget"
    if elapsed - waiting >= timeout:
        return f"agent exceeded its {timeout}s timeout"
    return None


def task_input_wait_seconds(task, *, now=None):
    started = task.provisioning_at or task.started_at or task.queued_at
    if started is None or not task.input_wait_budget_seconds:
        return 0.0
    now = now or timezone.now()
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
    spawner_for_task(task).reserve_input_wait(task, INPUT_WAIT_BUDGET_SECONDS)
    if task.model_gateway_agent_id:
        from astrolift_dispatch.model_gateway import renew_run_key, task_key_ttl

        # The Job may now outlive its gateway key (#1851). A failed renewal
        # refuses the question like a failed reservation, and the runner retries.
        renew_run_key(
            connection_id=task.model_gateway_connection_id,
            agent_id=task.model_gateway_agent_id,
            ttl_seconds=task_key_ttl(task) + INPUT_WAIT_BUDGET_SECONDS,
        )
    # A slow cluster API call also spends execution time before the question
    # exists. Do not acknowledge a new request after that budget has elapsed.
    if reason := task_timeout_reason(task):
        raise RuntimeError(reason)
    task.input_wait_budget_seconds = INPUT_WAIT_BUDGET_SECONDS
    task.save(update_fields=["input_wait_budget_seconds", "updated_at", "version"])
