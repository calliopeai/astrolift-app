"""
TearDownPreviewWorkflow — clean up a per-PR preview environment.

Triggered when a PR closes (or merges with auto-teardown enabled).
Removes the preview's namespace, ingress, DNS records, and any
short-lived managed services.
"""

from __future__ import annotations

from temporalio import workflow

from astrolift_workflows.inputs import TearDownPreviewInput, WorkflowResult


@workflow.defn(name="TearDownPreviewWorkflow")
class TearDownPreviewWorkflow:
    @workflow.run
    async def run(self, input: TearDownPreviewInput) -> WorkflowResult:
        # Skeleton: real implementation deletes namespace, ingress,
        # DNS records and marks PreviewEnvironment.status=torn_down.
        return WorkflowResult(ok=True, message="preview torn down")
