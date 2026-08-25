"""
BuildPreviewWorkflow — provision a per-PR or manually-triggered preview environment (#751).

Triggered by the GitHub PR webhook (opened / synchronize events) and by the
``createPreviewEnvironment`` GraphQL mutation when an operator manually
requests a preview from the UI.

Steps:
1. Mark PreviewEnvironment → BUILDING (re-entrant; RUNNING/FAILED → BUILDING).
2. Provision the preview-specific Kubernetes namespace (idempotent).
3. Mark PreviewEnvironment → RUNNING.

The actual image build + deploy happens through the CI pipeline:
the astrolift-ci workflow (pushed by ``sync_workflow_file_to_repo``) builds
the image and calls the ``deploy_app`` API endpoint with the resulting image
tag, which starts ``DeployAppWorkflow`` against the preview AppEnvironment.
This workflow only needs to bring the namespace up; the deploy follows via
the CI callback.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from astrolift_workflows.inputs import BuildPreviewInput, WorkflowResult

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        mark_preview_building,
        mark_preview_failed,
        mark_preview_running,
        provision_preview_managed_services_activity,
        provision_preview_namespace,
    )


_STEP_TIMEOUT = timedelta(minutes=10)


@workflow.defn(name="BuildPreviewWorkflow")
class BuildPreviewWorkflow:
    @workflow.run
    async def run(self, input: BuildPreviewInput) -> WorkflowResult:
        pid = input.preview_environment_id

        await workflow.execute_activity(
            mark_preview_building,
            pid,
            start_to_close_timeout=_STEP_TIMEOUT,
        )

        try:
            namespace = await workflow.execute_activity(
                provision_preview_namespace,
                pid,
                start_to_close_timeout=_STEP_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 - workflow error envelope
            await workflow.execute_activity(
                mark_preview_failed,
                pid,
                f"namespace provision failed: {exc}",
                start_to_close_timeout=_STEP_TIMEOUT,
            )
            return WorkflowResult(ok=False, message=f"preview namespace provision failed: {exc}")

        # PROVISION_MANAGED_SERVICES (#1578). Named in
        # `preview_build.BUILD_ORDER` since that tuple was written and
        # performed by nothing until now, which is why a preview booted with
        # no DB / redis / queue envelope.
        #
        # Non-fatal by design. A preview with a namespace and no database is
        # useful -- the operator can look at why -- where failing the whole
        # build leaves them with neither and no diagnosis. The outcome's
        # `shared_unsliced` and `errors` carry what did not happen.
        services: dict = {}
        try:
            services = await workflow.execute_activity(
                provision_preview_managed_services_activity,
                pid,
                start_to_close_timeout=_STEP_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 - workflow error envelope
            workflow.logger.warning("preview managed services failed: %s", exc)

        await workflow.execute_activity(
            mark_preview_running,
            pid,
            start_to_close_timeout=_STEP_TIMEOUT,
        )
        attached = ", ".join(services.get("attached") or []) or "none"
        return WorkflowResult(
            ok=True,
            message=f"preview namespace {namespace} ready; services attached: {attached}",
        )
