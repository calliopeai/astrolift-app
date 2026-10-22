"""Post a settled scheduled run's result to its delivery channels (#2244).

The activity carries only the run's row id and the outcome the schedule
wrapper saw. Channel credentials are read inside ``workflows.delivery`` and
never enter workflow history. A transient failure raises so Temporal retries;
targets already delivered are skipped on the retry, and the last attempt
records what is still failing instead of raising.
"""

from __future__ import annotations

from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from astrolift_workflows.activity_heartbeat import heartbeat_while

# The wrapper's retry budget; the activity reads it to know its last attempt.
DELIVERY_MAX_ATTEMPTS = 5


@activity.defn(name="astrolift.configured_workflow.deliver_run_result")
async def deliver_workflow_run_result(request: dict[str, Any]) -> dict[str, Any]:
    from asgiref.sync import sync_to_async

    from workflows.delivery import deliver_run

    final_attempt = activity.info().attempt >= DELIVERY_MAX_ATTEMPTS
    summary = await heartbeat_while(
        sync_to_async(deliver_run)(
            int(request["workflow_run_id"]),
            outcome=str(request.get("outcome") or ""),
            final_attempt=final_attempt,
        )
    )
    if summary.retrying:
        raise ApplicationError(f"{summary.retrying} delivery target(s) will be retried")
    return summary.as_dict()
