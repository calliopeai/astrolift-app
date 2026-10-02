"""Completion delivery activities expose only IDs and safe scheduling facts."""

from __future__ import annotations

import math

from asgiref.sync import sync_to_async
from temporalio import activity


@activity.defn(name="astrolift.agent_task_callback.deliver")
async def deliver_agent_task_callback(callback_id: int, generation: int) -> dict:
    from astrolift_agents.services.task_completion_callbacks import deliver_attempt

    try:
        outcome = await sync_to_async(deliver_attempt, thread_sensitive=False)(callback_id, generation)
        state = outcome["state"]
        if state not in {"finished", "retry", "waiting"}:
            raise ValueError("Invalid completion delivery state")
        delay = float(outcome.get("delay_seconds", 0.0))
        if not math.isfinite(delay):
            raise ValueError("Invalid completion delivery delay")
        # Project rather than forward service objects. This boundary prevents
        # future transport diagnostics from entering SDK results or history.
        return {
            "state": state,
            "delay_seconds": min(4320.0, max(0.0, delay)),
            "attempts": max(0, int(outcome.get("attempts", 0))),
        }
    except Exception:
        # Database/worker outages must not serialize arbitrary exception text,
        # request bodies, or secret values into Temporal failures or its logs.
        # The durable row owns the deadline; retrying resumes its lease/state.
        return {"state": "retry", "delay_seconds": 30.0, "attempts": 0}


def _reconcile_agent_task_callbacks_sync() -> int:
    from django.db.models import Q
    from django.utils import timezone

    from astrolift_agents.models import AgentTaskCompletionCallback
    from astrolift_agents.services.task_completion_callbacks import enqueue_callback

    now = timezone.now()
    rows = (
        AgentTaskCompletionCallback.objects.filter(
            status=AgentTaskCompletionCallback.Status.PENDING,
            final_event_id__isnull=False,
            next_attempt_at__lte=now,
        )
        .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
        .order_by("next_attempt_at", "pk")
        .values_list("pk", "generation")[:500]
    )
    enqueued = 0
    failures = 0
    for callback_id, generation in rows:
        # One failing start must not prevent unrelated durable rows recovering.
        try:
            started = bool(enqueue_callback(callback_id, generation))
        except Exception:
            started = False
        enqueued += started
        failures = 0 if started else failures + 1
        if failures >= 3:
            # A disconnected Temporal service must not leave a 500-row sweep
            # blocking a worker thread for hours. Rows remain due next minute.
            break
    return enqueued


@activity.defn(name="astrolift.scheduled.reconcile_agent_task_callbacks")
async def reconcile_agent_task_callbacks() -> int:
    return await sync_to_async(_reconcile_agent_task_callbacks_sync, thread_sensitive=False)()
