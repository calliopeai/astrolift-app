"""
PromoteDeploymentWorkflow — copy a running deployment to a different env.

Picks up a ``source_deployment_id`` (typically staging) and a
``target_app_environment_id`` (typically prod), creates a new
``trigger_kind=promotion`` deployment row pointing back at the
source via ``promoted_from``, then runs the app's supply-chain gate
(#313) and, if it passes, the standard apply path against it.

If the target env requires approvals, the new row lands in
``pending_approval`` and the workflow exits — the operator approves
through the existing approve_deployment mutation, which re-starts
DeployAppWorkflow against the row. (We could wait for the signal
here, but the existing approve flow already does the right thing
and avoids workflow-ID collisions on long-pending approvals.)
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from astrolift_workflows.inputs import PromoteInput, WorkflowResult


with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        apply_manifests,
        create_promotion_deployment,
        evaluate_supply_chain_gate,
        health_check,
        mark_deploying,
        mark_failed,
        mark_running,
        poll_rollout,
        pre_flight,
        render_manifests,
        update_secrets,
        wait_dns,
    )


_TIMEOUT = timedelta(minutes=15)


@workflow.defn(name="PromoteDeploymentWorkflow")
class PromoteDeploymentWorkflow:
    @workflow.run
    async def run(self, input: PromoteInput) -> WorkflowResult:
        new_id: int = await workflow.execute_activity(
            create_promotion_deployment,
            args=[input.source_deployment_id, input.target_app_environment_id],
            start_to_close_timeout=_TIMEOUT,
        )

        # Supply-chain gate (#313). Runs before anything touches the
        # cluster — and before the approval hand-off below — so an image
        # the app's own policy refuses never reaches an operator's
        # approval queue, let alone the apply path.
        gate: dict = await workflow.execute_activity(
            evaluate_supply_chain_gate,
            new_id,
            start_to_close_timeout=_TIMEOUT,
        )
        if gate.get("decision") == "block":
            reason = gate.get("reason") or "blocked by the app's supply-chain policy"
            await workflow.execute_activity(
                mark_failed,
                args=[new_id, reason],
                start_to_close_timeout=_TIMEOUT,
            )
            return WorkflowResult(
                ok=False,
                message=reason,
                data={"new_deployment_id": new_id, "supply_chain": gate},
            )

        # Resolve the new row's status. If approvals are required,
        # the row landed pending_approval and we hand off to the
        # approve mutation's existing flow.
        from asgiref.sync import sync_to_async  # noqa: E402  (workflow-context import)

        # We can't query Django from a workflow without a sandbox
        # exception; instead branch in the apply path itself by
        # letting pre_flight fail-fast if the row is still pending
        # approval. For the common no-approvals case this just runs
        # straight through.
        await workflow.execute_activity(pre_flight, new_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(mark_deploying, new_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(render_manifests, new_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(apply_manifests, new_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(update_secrets, new_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(wait_dns, new_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(poll_rollout, new_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(health_check, new_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(mark_running, new_id, start_to_close_timeout=_TIMEOUT)

        return WorkflowResult(
            ok=True,
            message="promoted",
            data={"new_deployment_id": new_id},
        )
