"""
TearDownPreviewWorkflow — clean up a per-PR preview environment.

Triggered when a PR closes (or merges with auto-teardown enabled).
Deletes the preview namespace (which cascades every resource the apply
path created — Deployments, Services, Ingresses, Secrets, ConfigMaps,
PVCs) and flips the PreviewEnvironment row to TORN_DOWN.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from astrolift_workflows.inputs import TearDownPreviewInput, WorkflowResult

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        delete_preview_namespace,
        mark_preview_torn_down,
    )


_TIMEOUT = timedelta(minutes=10)


@workflow.defn(name="TearDownPreviewWorkflow")
class TearDownPreviewWorkflow:
    @workflow.run
    async def run(self, input: TearDownPreviewInput) -> WorkflowResult:
        try:
            namespace = await workflow.execute_activity(
                delete_preview_namespace,
                input.preview_environment_id,
                start_to_close_timeout=_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 - workflow error envelope
            return WorkflowResult(ok=False, message=f"namespace delete failed: {exc}")

        await workflow.execute_activity(
            mark_preview_torn_down,
            input.preview_environment_id,
            start_to_close_timeout=_TIMEOUT,
        )
        return WorkflowResult(ok=True, message=f"preview namespace {namespace} torn down")
