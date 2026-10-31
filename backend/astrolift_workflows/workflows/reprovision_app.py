"""ReprovisionAppWorkflow: re-apply one environment against its running release (#2573).

A deploy was the only way to re-render an environment, and it rebuilds and
rolls a new image to get there. This runs the deploy's own platform steps
against the ``trigger_kind=reprovision`` row ``reprovisionApp`` created,
which carries the running image tag and digest and that release's literal
secrets, so nothing is rebuilt and a pod restarts only when the rendered
workload differs from what is running.

Left out of the deploy's sequence: the manifest resync (it would pull a
manifest nobody has deployed), the build and the static asset sync (no new
release), and the DNS propagation wait (it requests nothing). Server-side
apply creates what is missing and patches what drifted; nothing is deleted.

The workflow id is the environment's deploy workflow id, so a newer deploy
signals ``abort`` and replaces it the way it replaces an older deploy.
"""

from __future__ import annotations

from temporalio import workflow
from temporalio.exceptions import ActivityError

from astrolift_workflows.inputs import ReprovisionInput, WorkflowResult
from astrolift_workflows.workflows.deploy_app import (
    _BUILD_TIMEOUT,
    _MARK_TIMEOUT,
    _ROLLOUT_RETRY,
    _STANDARD_RETRY,
    _TIMEOUT,
    _failure_reason,
)

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        apply_manifests,
        ensure_workload_identity,
        health_check,
        mark_deploying,
        mark_failed,
        mark_running,
        poll_rollout,
        pre_flight,
        provision_namespace,
        restore_previous_secrets,
        update_secrets,
    )
    from astrolift_workflows.activities.faas import ensure_faas_services
    from astrolift_workflows.activities.static_site import (
        ensure_cloudfront_cert,
        ensure_static_dns,
        ensure_static_site_services,
    )
    from astrolift_workflows.activity_heartbeat import HEARTBEAT_TIMEOUT


@workflow.defn(name="ReprovisionAppWorkflow")
class ReprovisionAppWorkflow:
    def __init__(self) -> None:
        self._abort_requested = False
        self._applied: dict = {}

    @workflow.signal(name="abort")
    def request_abort(self) -> None:
        self._abort_requested = True

    @workflow.run
    async def run(self, input: ReprovisionInput) -> WorkflowResult:
        deployment_id = input.deployment_id
        environment = [input.registered_app_id, input.app_environment_id]
        try:
            await workflow.execute_activity(
                pre_flight,
                deployment_id,
                start_to_close_timeout=_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
            await workflow.execute_activity(
                mark_deploying,
                deployment_id,
                start_to_close_timeout=_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
            await workflow.execute_activity(
                provision_namespace,
                args=environment,
                start_to_close_timeout=_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
            await workflow.execute_activity(
                ensure_cloudfront_cert,
                deployment_id,
                start_to_close_timeout=_BUILD_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
            await workflow.execute_activity(
                ensure_static_site_services,
                deployment_id,
                start_to_close_timeout=_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
            await workflow.execute_activity(
                ensure_faas_services,
                deployment_id,
                start_to_close_timeout=_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
            await workflow.execute_activity(
                ensure_workload_identity,
                args=environment,
                start_to_close_timeout=_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
            # Secrets before the workloads that read them, as the deploy does (#1758).
            await workflow.execute_activity(
                update_secrets,
                deployment_id,
                start_to_close_timeout=_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
            self._applied = await workflow.execute_activity(
                apply_manifests,
                deployment_id,
                start_to_close_timeout=_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
            await workflow.execute_activity(
                ensure_static_dns,
                deployment_id,
                start_to_close_timeout=_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
            if self._abort_requested:
                await workflow.execute_activity(
                    mark_failed,
                    args=[deployment_id, "aborted: superseded by a newer deploy or cancelled"],
                    start_to_close_timeout=_MARK_TIMEOUT,
                    heartbeat_timeout=HEARTBEAT_TIMEOUT,
                    retry_policy=_STANDARD_RETRY,
                )
                return WorkflowResult(ok=False, message="aborted by signal", data=self._applied)
            await workflow.execute_activity(
                poll_rollout,
                deployment_id,
                start_to_close_timeout=_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=_ROLLOUT_RETRY,
            )
            await workflow.execute_activity(
                health_check,
                deployment_id,
                start_to_close_timeout=_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=_ROLLOUT_RETRY,
            )
            await workflow.execute_activity(
                mark_running,
                deployment_id,
                start_to_close_timeout=_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
            return WorkflowResult(ok=True, message="reprovisioned", data=self._applied)
        except ActivityError as exc:
            reason = _failure_reason(exc)
            try:
                await workflow.execute_activity(
                    restore_previous_secrets,
                    deployment_id,
                    start_to_close_timeout=_MARK_TIMEOUT,
                    heartbeat_timeout=HEARTBEAT_TIMEOUT,
                    retry_policy=_STANDARD_RETRY,
                )
            except Exception:  # noqa: BLE001 - must not hide the failure or skip mark_failed
                workflow.logger.exception("restore_previous_secrets failed for deployment %s", deployment_id)
            await workflow.execute_activity(
                mark_failed,
                args=[deployment_id, reason],
                start_to_close_timeout=_MARK_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
            return WorkflowResult(ok=False, message=f"reprovision failed: {reason}", data=self._applied)
