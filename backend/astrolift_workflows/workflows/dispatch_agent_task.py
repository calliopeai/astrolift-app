"""DispatchAgentTaskWorkflow (spec 33, PR-1).

The durable Once-dispatch wrapper for a registered agent
``Workload(kind=agent)``. The ``runAstroliftAgent`` mutation has already
created the ``AgentTask`` (in QUEUED, with its ``agent_definition`` Workload
set) and started this workflow with the task's pk; the workflow runs that
single task to a terminal state by invoking the ``dispatch_agent_task``
activity, which owns the whole spawn -> poll -> terminal lifecycle (it
reuses the same ``_spawn_agent_task_sync`` / ``_poll_agent_task_sync``
helpers as ``execute_agent_stage``, minus the task-creation step the
mutation already performed).

The activity is one long-running, self-heartbeating call, so this
workflow is intentionally thin — there is no per-job DAG to fan out for a
single ad-hoc dispatch. On the activity timing out or failing the workflow
records a clean failure; the activity itself best-effort-cancels the
in-flight task on Temporal cancellation.

Workflow id pattern: ``DispatchAgentTaskWorkflow-<task-guid>`` (the
mutation constructs it that way) so a duplicate fire of the same task
joins the in-flight run instead of spawning a parallel dispatch.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import DispatchAgentTaskInput, WorkflowResult

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.agent_stage import dispatch_agent_task


# The dispatch activity owns the full spawn + poll loop and heartbeats on
# each cycle, so it can legitimately run for the task's whole lifetime.
# Bound the single execution generously (a long agent run) and let the
# heartbeat — not a short start-to-close — be what detects a wedged worker.
_DISPATCH_TIMEOUT = timedelta(hours=24)
_HEARTBEAT_TIMEOUT = timedelta(minutes=2)
# Do NOT auto-retry the whole dispatch: a single attempt either drives the
# task to terminal or records a terminal FAILED on the task itself (the
# spawn helper flips the task to FAILED on a spawn error). Re-running would
# re-spawn a fresh container for an already-terminal task. One attempt.
_NO_RETRY = RetryPolicy(maximum_attempts=1)


@workflow.defn(name="DispatchAgentTaskWorkflow")
class DispatchAgentTaskWorkflow:
    @workflow.run
    async def run(self, input: DispatchAgentTaskInput) -> WorkflowResult:
        try:
            outcome = await workflow.execute_activity(
                dispatch_agent_task,
                input.agent_task_id,
                start_to_close_timeout=_DISPATCH_TIMEOUT,
                heartbeat_timeout=_HEARTBEAT_TIMEOUT,
                retry_policy=_NO_RETRY,
            )
        except Exception as exc:  # noqa: BLE001 — surface as a clean result
            return WorkflowResult(
                ok=False,
                message=f"agent dispatch failed: {exc}",
            )

        status = outcome.get("status", "")
        ok = status == "completed"
        return WorkflowResult(
            ok=ok,
            message=f"agent task {status}" if status else "agent task dispatched",
            data=outcome,
        )
