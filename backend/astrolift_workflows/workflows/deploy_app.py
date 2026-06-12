"""
DeployAppWorkflow — realize one rollout to one (app, env).

Mirrors specs/06 §4.2. Single-flight per (app, env) is enforced by
the workflow id: ``DeployAppWorkflow-<app-guid>-<env-guid>``. A
duplicate-start signal is wired up below so a newer deploy aborts
the in-flight one.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

from astrolift_workflows.inputs import DeployAppInput, WorkflowResult

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        apply_manifests,
        health_check,
        mark_deploying,
        mark_running,
        poll_rollout,
        pre_flight,
        provision_namespace,
        render_manifests,
        update_secrets,
        wait_dns,
    )
    from astrolift_workflows.activities.build_image import (
        BuildImageInput,
        build_image,
        fetch_app_build_strategy,
    )


# Fargate cold-starts + image-pull can exceed 15m on first rollout (#359).
_TIMEOUT = timedelta(minutes=20)


@workflow.defn(name="DeployAppWorkflow")
class DeployAppWorkflow:
    def __init__(self) -> None:
        self._abort_requested: bool = False

    @workflow.signal(name="abort")
    def request_abort(self) -> None:
        self._abort_requested = True

    @workflow.run
    async def run(self, input: DeployAppInput) -> WorkflowResult:
        # The mutation creates the Deployment row + workflow_run, passes
        # the deployment_id in here, and the workflow operates on that
        # specific row. Earlier revisions used app_environment_id as
        # placeholder — that always loaded the wrong record because the
        # activities key on the deployment, not the env.
        deployment_id = input.deployment_id

        await workflow.execute_activity(pre_flight, deployment_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(mark_deploying, deployment_id, start_to_close_timeout=_TIMEOUT)

        # Build step (#865, #867): when build_strategy != "off" the platform
        # builds the container image from source before deploying. The
        # fetch_app_build_strategy activity keeps Django imports out of this
        # sandbox. We use the first workload's image_tag from the input as
        # the target tag; when multiple workloads are present the first tag
        # is used as the build output and all workloads share it — this is
        # intentional for v1 (one build per deploy).
        build_strategy = await workflow.execute_activity(
            fetch_app_build_strategy,
            args=[input.registered_app_id],
            start_to_close_timeout=_TIMEOUT,
        )
        if build_strategy != "off":
            _image_tags: dict = input.image_tags or {}
            _image_tag = next(iter(_image_tags.values()), "")
            _commit_sha = ""  # TODO: thread commit_sha through DeployAppInput (#865)
            await workflow.execute_activity(
                build_image,
                args=[BuildImageInput(
                    app_guid=str(input.registered_app_id),
                    image_tag=_image_tag,
                    commit_sha=_commit_sha,
                )],
                start_to_close_timeout=_TIMEOUT,
            )

        # provision_namespace runs *before* render so the namespace
        # exists when apply_manifests creates Services/Secrets in it.
        await workflow.execute_activity(
            provision_namespace,
            args=[input.registered_app_id, input.app_environment_id],
            start_to_close_timeout=_TIMEOUT,
        )
        await workflow.execute_activity(render_manifests, deployment_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(apply_manifests, deployment_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(update_secrets, deployment_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(wait_dns, deployment_id, start_to_close_timeout=_TIMEOUT)

        if self._abort_requested:
            return WorkflowResult(ok=False, message="aborted by signal")

        await workflow.execute_activity(poll_rollout, deployment_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(health_check, deployment_id, start_to_close_timeout=_TIMEOUT)
        await workflow.execute_activity(mark_running, deployment_id, start_to_close_timeout=_TIMEOUT)

        return WorkflowResult(ok=True, message="deployed")
