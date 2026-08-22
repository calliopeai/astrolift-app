"""
TearDownPreviewWorkflow — clean up a per-PR preview environment.

Triggered when a PR closes (or merges with auto-teardown enabled).
Walks the ordered teardown sequence declared by
``astrolift_workflows.preview_teardown`` (the pure-policy module):
namespace, DNS, managed services, mark, event, PR comment. Which of
those steps run at all is the policy's call — ``teardown_steps_to_run``
short-circuits an already-torn-down preview and returns only the
bookkeeping tail for a row whose status and timestamp disagree, so a
re-fire converges instead of re-deleting and re-stamping.

Per-step timeouts come from the policy's budget table, which sums to
the spec's 15-minute teardown deadline; the previous flat 10-minute
timeout per activity could overrun it by minutes.

DELETE_DNS, CLEANUP_MANAGED_SERVICES and COMMENT_PR are in the
sequence but have no activity yet — the platform creates no per-preview
DNS record (the wildcard covers the host), carves no per-preview
managed-service slice, and has no PR-comment surface at all. They are
skipped rather than faked; the loop below picks them up for free once
their activities land.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from astrolift_workflows.inputs import TearDownPreviewInput, WorkflowResult

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        delete_preview_namespace,
        emit_preview_torn_down_event,
        load_preview_teardown_state,
        mark_preview_torn_down,
    )
    from astrolift_workflows.preview_teardown import (
        PreviewTeardownState,
        TeardownStep,
        deadline_for_step,
        teardown_steps_to_run,
    )


# Reading one row to build the policy's projection; not one of the
# budgeted teardown steps, so it carries its own short timeout.
_PROJECTION_TIMEOUT = timedelta(minutes=1)


@workflow.defn(name="TearDownPreviewWorkflow")
class TearDownPreviewWorkflow:
    @workflow.run
    async def run(self, input: TearDownPreviewInput) -> WorkflowResult:
        pid = input.preview_environment_id

        state = await workflow.execute_activity(
            load_preview_teardown_state,
            pid,
            start_to_close_timeout=_PROJECTION_TIMEOUT,
        )
        steps = teardown_steps_to_run(state=PreviewTeardownState(**state))
        if not steps:
            return WorkflowResult(ok=True, message=f"preview {pid} already torn down")

        namespace = ""
        skipped: list[str] = []
        for step in steps:
            timeout = timedelta(seconds=deadline_for_step(step=step))
            if step is TeardownStep.DELETE_NAMESPACE:
                try:
                    namespace = await workflow.execute_activity(
                        delete_preview_namespace,
                        pid,
                        start_to_close_timeout=timeout,
                    )
                except Exception as exc:  # noqa: BLE001 - workflow error envelope
                    # Bail out: everything after this step assumes the
                    # workloads are gone, and marking a preview torn
                    # down while its pods still run is a worse state
                    # than a failed teardown an operator can re-fire.
                    return WorkflowResult(ok=False, message=f"namespace delete failed: {exc}")
            elif step is TeardownStep.MARK_TORN_DOWN:
                await workflow.execute_activity(
                    mark_preview_torn_down,
                    pid,
                    start_to_close_timeout=timeout,
                )
            elif step is TeardownStep.EMIT_EVENT:
                await workflow.execute_activity(
                    emit_preview_torn_down_event,
                    pid,
                    start_to_close_timeout=timeout,
                )
            else:
                skipped.append(str(step))

        message = f"preview namespace {namespace} torn down" if namespace else f"preview {pid} torn down"
        if skipped:
            message = f"{message} (no activity yet: {', '.join(skipped)})"
        return WorkflowResult(ok=True, message=message)
