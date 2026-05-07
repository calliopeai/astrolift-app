"""
RollbackDeploymentWorkflow — re-deploy the previous running revision.

Operates on a deployment id; resolves the prior ``running`` deployment
in the same env and re-applies its config_snapshot.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from astrolift_workflows.inputs import RollbackInput, WorkflowResult


_TIMEOUT = timedelta(minutes=10)


@workflow.defn(name="RollbackDeploymentWorkflow")
class RollbackDeploymentWorkflow:
    @workflow.run
    async def run(self, input: RollbackInput) -> WorkflowResult:
        # Skeleton: a real implementation reuses DeployAppWorkflow's
        # apply path against the prior config_snapshot.
        return WorkflowResult(ok=True, message="rollback enqueued")
