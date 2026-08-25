"""CommandRunWorkflow + RunScheduledJobWorkflow (#1600).

`astrolift_workflows.command_run` opens by naming these two and describing
what each does. Neither existed, so the module -- timeout bounds, the Job
spec plan, failure classification, the concurrency guard -- had no caller,
and the `CommandRun` / `ScheduledJobRun` models, their admin and their
GraphQL types had nothing writing terminal states.

The shared spine is here once and `RunScheduledJobWorkflow` delegates to it.
Per spec 06 §4.20a the only thing the scheduled variant adds is the
concurrency guard, and forking the spine to add one check is how the two
drift into behaving differently on timeouts.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.command_run_exec import (
        finish_command_run,
        poll_command_run,
        start_command_run,
    )

_START_TIMEOUT = timedelta(seconds=60)
_POLL_TIMEOUT = timedelta(seconds=30)

#: How often to ask the cluster. Not configurable: a one-shot command is
#: interactive, and the operator is watching.
_POLL_INTERVAL = timedelta(seconds=5)


async def _run_to_completion(command_run_id: int) -> dict[str, Any]:
    """Start the Job, poll until it settles, record the outcome.

    The wall-clock bound comes from the Job's own ``activeDeadlineSeconds``
    (`JobSpecPlan.timeout_seconds`), not from a timer here. Kubernetes is
    what actually kills the pod, so a second timeout in the workflow could
    only disagree with it -- and would report a failure for a command still
    running.

    The poll loop is still bounded, generously, against the Job never
    settling at all: a cluster that stops answering leaves this workflow
    otherwise waiting forever.
    """
    started = await workflow.execute_activity(
        start_command_run,
        command_run_id,
        start_to_close_timeout=_START_TIMEOUT,
    )

    deadline_polls = max(1, int(started["timeout"] / _POLL_INTERVAL.total_seconds()) + 12)

    outcome: dict[str, Any] = {"state": "failed", "conditions": ["command run never settled"]}
    for _ in range(deadline_polls):
        await workflow.sleep(_POLL_INTERVAL)
        result = await workflow.execute_activity(
            poll_command_run,
            command_run_id,
            start_to_close_timeout=_POLL_TIMEOUT,
        )
        if result["state"] != "running":
            outcome = result
            break

    return await workflow.execute_activity(
        finish_command_run,
        args=[command_run_id, outcome],
        start_to_close_timeout=_START_TIMEOUT,
    )


@workflow.defn(name="CommandRunWorkflow")
class CommandRunWorkflow:
    @workflow.run
    async def run(self, command_run_id: int) -> dict[str, Any]:
        return await _run_to_completion(command_run_id)


@workflow.defn(name="RunScheduledJobWorkflow")
class RunScheduledJobWorkflow:
    """A CronJob's "Run Now", with the guard from spec 06 §4.20a.

    The guard runs in the workflow rather than at the API boundary because
    that is where the answer stays true: two presses a second apart both pass
    a resolver-side check, and only one of them should produce a Job.
    """

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        from astrolift_workflows.command_run import ConcurrencyError

        try:
            await workflow.execute_activity(
                "astrolift.command_run.assert_no_concurrent",
                payload,
                start_to_close_timeout=_POLL_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            # A refused run is an answer, not a workflow failure: the caller
            # asked whether it could run and the answer is no. Raising would
            # put a red workflow in the operator's history for a guard doing
            # its job.
            if ConcurrencyError.__name__ in str(exc):
                return {"state": "refused", "reason": str(exc)}
            raise

        return await _run_to_completion(int(payload["command_run_id"]))
