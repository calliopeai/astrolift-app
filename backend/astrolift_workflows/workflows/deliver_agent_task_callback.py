"""Durable completion retries without sensitive payloads in workflow history."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.agent_task_callback import (
        deliver_agent_task_callback,
        reconcile_agent_task_callbacks,
    )


@workflow.defn(name="DeliverAgentTaskCallbackWorkflow")
class DeliverAgentTaskCallbackWorkflow:
    @workflow.run
    async def run(self, callback_id: int, generation: int) -> dict:
        iterations = 0
        while True:
            try:
                outcome = await workflow.execute_activity(
                    deliver_agent_task_callback,
                    args=[callback_id, generation],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
            except ActivityError:
                # A worker can disappear after the receiver accepts a POST.
                # The next activity resumes the persisted lease and may resend;
                # automatic activity retries do not own the HTTP retry cadence.
                outcome = {"state": "retry", "delay_seconds": 30.0, "attempts": 0}
            if outcome["state"] == "finished":
                return outcome
            await workflow.sleep(timedelta(seconds=max(0.1, outcome["delay_seconds"])))
            iterations += 1
            if iterations >= 100:
                # Extended database outages do not grow workflow history without
                # bound. Delivery deadline, attempts and lease remain in the row.
                workflow.continue_as_new(args=[callback_id, generation])


@workflow.defn(name="AgentTaskCallbackReconcileWorkflow")
class AgentTaskCallbackReconcileWorkflow:
    @workflow.run
    async def run(self) -> int:
        return await workflow.execute_activity(
            reconcile_agent_task_callbacks,
            start_to_close_timeout=timedelta(minutes=5),
        )
