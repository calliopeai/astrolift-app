"""
RollbackDeploymentWorkflow — re-deploy the previous running revision.

Operates on a deployment id (the bad deploy). The workflow:

1. Resolves the most recent prior ``running`` (or ``superseded``)
   deployment in the same env, copies its image_tag + config snapshot
   into a new ``trigger_kind=rollback`` deployment row, and marks the
   bad deploy SUPERSEDED. ``create_rollback_deployment`` activity
   does this in one DB transaction.
2. Runs the same apply path as DeployAppWorkflow against the new
   row: pre_flight → mark_deploying → render → secrets → apply →
   wait_dns → poll → health → mark_running.

The rollback path deliberately reuses the deploy activities rather
than carrying its own copy — they're already idempotent, and a
rollback that goes through a different code path could surprise
operators by behaving differently.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from astrolift_workflows.inputs import RollbackInput, WorkflowResult


with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        apply_manifests,
        create_rollback_deployment,
        health_check,
        mark_deploying,
        mark_running,
        poll_rollout,
        pre_flight,
        render_manifests,
        update_secrets,
        wait_dns,
    )


_TIMEOUT = timedelta(minutes=15)


@workflow.defn(name="RollbackDeploymentWorkflow")
class RollbackDeploymentWorkflow:
    @workflow.run
    async def run(self, input: RollbackInput) -> WorkflowResult:
        # Resolve prior + create new deployment row in one go.
        new_id: int = await workflow.execute_activity(
            create_rollback_deployment,
            input.deployment_id,
            start_to_close_timeout=_TIMEOUT,
        )

        # Same apply path as DeployAppWorkflow. Reuse > duplicate.
        await workflow.execute_activity(pre_flight, new_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(mark_deploying, new_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(render_manifests, new_id, start_to_close_timeout=_TIMEOUT)
        # The Secrets before the workloads that read them, for the reason
        # DeployAppWorkflow gives (#1758). Patched for replay.
        if workflow.patched("deploy-secrets-before-apply"):
            await workflow.execute_activity(update_secrets, new_id, start_to_close_timeout=_TIMEOUT)
            await workflow.execute_activity(apply_manifests, new_id, start_to_close_timeout=_TIMEOUT)
        else:
            await workflow.execute_activity(apply_manifests, new_id, start_to_close_timeout=_TIMEOUT)
            await workflow.execute_activity(update_secrets, new_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(wait_dns, new_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(poll_rollout, new_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(health_check, new_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(mark_running, new_id, start_to_close_timeout=_TIMEOUT)

        return WorkflowResult(
            ok=True,
            message="rolled back to previous running revision",
            data={"new_deployment_id": new_id},
        )
