"""AppExpiryWorkflow (#2433): the durable timer of one temporary app.

Id ``AppExpiryWorkflow-<app guid>``. A loop over ``app_expiry_next_step``,
which reads the app row and answers sleep, fire, teardown, purge or done (see
``astrolift_registry.expiry.next_step``). Setting, extending, shortening or
clearing the expiry writes the row and sends ``reschedule``, which ends the
current sleep early; a worker restart replays the history and the timer keeps
its place. ``AppExpirySweepWorkflow`` starts any timer that went missing.

On fire: scale to zero (archive), then ``DeregisterAppWorkflow`` as a child
with the managed services kept (unless the app deletes its data on expiry)
and no cancel window, then the ``app.expiry.fire`` audit row. After the
organization's grace period the kept managed services are deprovisioned with
their data.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import Actor, AppExpiryInput, DeregisterAppInput, WorkflowResult

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        app_expiry_next_step,
        deprovision_managed_service,
        finalize_managed_service_deletion,
        fire_app_expiry,
        purge_expired_app_namespaces,
        record_app_expiry_purge,
        record_app_expiry_teardown,
        sweep_app_expiry,
    )

_QUICK = timedelta(minutes=2)
_DEPROVISION = timedelta(minutes=30)
_RETRY = RetryPolicy(initial_interval=timedelta(seconds=5), maximum_interval=timedelta(minutes=1))
_CLEANUP_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5), maximum_interval=timedelta(seconds=60), maximum_attempts=3
)
#: How long to wait before retrying a teardown or purge that did not finish.
_RETRY_LATER = timedelta(hours=1)


@workflow.defn(name="AppExpiryWorkflow")
class AppExpiryWorkflow:
    def __init__(self) -> None:
        self._wake = False

    @workflow.signal(name="reschedule")
    def reschedule(self) -> None:
        """The expiry changed: re-read the row now."""
        self._wake = True

    async def _sleep(self, seconds: float) -> None:
        try:
            await workflow.wait_condition(lambda: self._wake, timeout=timedelta(seconds=seconds))
        except TimeoutError:
            pass

    @workflow.run
    async def run(self, input: AppExpiryInput) -> WorkflowResult:
        app_id = input.registered_app_id
        while True:
            # A signal received during the row read must invalidate its answer.
            self._wake = False
            step: dict[str, Any] = await workflow.execute_activity(
                app_expiry_next_step,
                app_id,
                start_to_close_timeout=_QUICK,
                retry_policy=_RETRY,
            )
            action = step.get("action")
            if action == "sleep":
                await self._sleep(float(step["seconds"]))
            elif action == "fire":
                await workflow.execute_activity(
                    fire_app_expiry, app_id, start_to_close_timeout=_QUICK, retry_policy=_RETRY
                )
            elif action == "teardown":
                if not await self._teardown(app_id, step):
                    await self._sleep(_RETRY_LATER.total_seconds())
            elif action == "purge":
                if not await self._purge(app_id, list(step["managed_service_ids"])):
                    await self._sleep(_RETRY_LATER.total_seconds())
            else:
                return WorkflowResult(ok=True, message="expiry timer done")

    async def _teardown(self, app_id: int, step: dict[str, Any]) -> bool:
        delete_data = bool(step.get("delete_data"))
        try:
            result = await workflow.execute_child_workflow(
                "DeregisterAppWorkflow",
                DeregisterAppInput(
                    registered_app_id=app_id,
                    actor=Actor(kind="system", display="app expiry"),
                    delete_data=delete_data,
                    keep_managed_services=not delete_data,
                    skip_grace=True,
                ),
                id=f"DeregisterAppWorkflow-{step['guid']}",
                result_type=WorkflowResult,
            )
        except Exception as exc:  # noqa: BLE001 - e.g. a person's deregister is already running
            workflow.logger.warning("expiry teardown of app %s did not run: %s", app_id, exc)
            return False
        await workflow.execute_activity(
            record_app_expiry_teardown,
            args=[app_id, result.data or {}],
            start_to_close_timeout=_QUICK,
            retry_policy=_RETRY,
        )
        return bool(result.ok)

    async def _purge(self, app_id: int, ids: list[int]) -> bool:
        results: list[dict[str, Any]] = []
        for ms_id in ids:
            try:
                res = await workflow.execute_activity(
                    deprovision_managed_service,
                    args=[ms_id, True, False],
                    start_to_close_timeout=_DEPROVISION,
                    retry_policy=_CLEANUP_RETRY,
                )
                ok = bool(res.get("ok"))
                if ok:
                    await workflow.execute_activity(
                        finalize_managed_service_deletion,
                        ms_id,
                        start_to_close_timeout=_QUICK,
                        retry_policy=_RETRY,
                    )
                results.append(
                    {"managed_service_id": ms_id, "ok": ok, "message": str(res.get("message", ""))}
                )
            except Exception as exc:  # noqa: BLE001 - one service must not strand the rest
                results.append({"managed_service_id": ms_id, "ok": False, "message": str(exc)})
        if all(r["ok"] for r in results):
            try:
                await workflow.execute_activity(
                    purge_expired_app_namespaces,
                    app_id,
                    start_to_close_timeout=_DEPROVISION,
                    retry_policy=_CLEANUP_RETRY,
                )
            except Exception as exc:  # noqa: BLE001 - retry retained namespaces with their original identity
                results.append({"namespace_cleanup": True, "ok": False, "message": str(exc)})
        await workflow.execute_activity(
            record_app_expiry_purge,
            args=[app_id, results],
            start_to_close_timeout=_QUICK,
            retry_policy=_RETRY,
        )
        return all(r["ok"] for r in results)


@workflow.defn(name="AppExpirySweepWorkflow")
class AppExpirySweepWorkflow:
    """Safety net (every 15 minutes): start the timer of any temporary app
    whose ``AppExpiryWorkflow`` is not running."""

    @workflow.run
    async def run(self) -> WorkflowResult:
        started = await workflow.execute_activity(
            sweep_app_expiry, start_to_close_timeout=timedelta(minutes=10)
        )
        return WorkflowResult(ok=True, message=f"armed {started} expiry timer(s)")
