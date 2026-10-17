"""BrainCreateWorkflow (#2424): create a semantic layer from ``createBrain``.

Steps, each an idempotent activity over one ``BrainCreation`` row:
repo, scaffold, register, deploy (once the app is provisioned), then watch
the first deployment until it runs. The workflow id is derived from the
organization and slug (``BrainCreateWorkflow-<org guid>-<slug>``), so a
retried ``createBrain`` joins the running execution.

A step that refuses records ``failed`` on the row itself; a step that keeps
failing after its retries is recorded as failed here. Nothing is undone: a
created repository or a registered app stays, and the error says which.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.brain_create import (
        brain_create_deploy,
        brain_create_fail,
        brain_create_observe,
        brain_create_register,
        brain_create_repo,
        brain_create_scaffold,
    )

_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=4,
)
_STEP_TIMEOUT = timedelta(minutes=10)
_POLL = timedelta(seconds=15)
#: How long the app may take to provision, and its first deployment to run.
_PROVISION_DEADLINE = timedelta(minutes=30)
_DEPLOY_DEADLINE = timedelta(minutes=45)


@workflow.defn(name="BrainCreateWorkflow")
class BrainCreateWorkflow:
    @workflow.run
    async def run(self, creation_id: int) -> str:
        try:
            for step in (brain_create_repo, brain_create_scaffold, brain_create_register):
                state = await self._step(step, creation_id)
                if state == "failed":
                    return state
            state = await self._wait(brain_create_deploy, creation_id, "registered", _PROVISION_DEADLINE)
            if state == "registered":
                return await self._fail(creation_id, "the app did not finish provisioning in time")
            if state != "deploying":
                return state
            state = await self._wait(brain_create_observe, creation_id, "deploying", _DEPLOY_DEADLINE)
            if state == "deploying":
                return await self._fail(creation_id, "the first deployment did not reach running in time")
            return state
        except ActivityError as exc:
            cause = getattr(exc, "cause", None)
            return await self._fail(creation_id, str(getattr(cause, "message", "") or cause or exc))

    async def _step(self, activity_fn, creation_id: int) -> str:
        result = await workflow.execute_activity(
            activity_fn, creation_id, start_to_close_timeout=_STEP_TIMEOUT, retry_policy=_RETRY
        )
        return str((result or {}).get("state") or "")

    async def _wait(self, activity_fn, creation_id: int, waiting: str, deadline: timedelta) -> str:
        started = workflow.now()
        while True:
            state = await self._step(activity_fn, creation_id)
            if state != waiting or workflow.now() - started >= deadline:
                return state
            await workflow.sleep(_POLL)

    async def _fail(self, creation_id: int, message: str) -> str:
        await workflow.execute_activity(
            brain_create_fail,
            args=[creation_id, "PRECONDITION", message[:2000]],
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=_RETRY,
        )
        return "failed"
