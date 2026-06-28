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
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from astrolift_workflows.inputs import DeployAppInput, WorkflowResult

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
        render_manifests,
        update_secrets,
        wait_dns,
    )
    from astrolift_workflows.activities.build_image import (
        BuildImageInput,
        build_image,
        fetch_app_build_strategy,
    )
    from astrolift_workflows.activities.static_site import (
        SyncStaticAssetsInput,
        ensure_cloudfront_cert,
        ensure_static_dns,
        ensure_static_site_services,
        sync_static_assets,
    )


# Fargate cold-starts + image-pull can exceed 15m on first rollout (#359).
_TIMEOUT = timedelta(minutes=20)

# Platform builds (kaniko from source) dominate deploy wall-clock — a cold
# dockerfile build clones, runs every layer, and pushes to the registry.
# Give the build step its own wider window (#978).
_BUILD_TIMEOUT = timedelta(minutes=30)

# Bounded retries so no deploy activity loops forever (#1004). Before this,
# activities ran under Temporal's default (unlimited) retry policy: a
# rollout that timed out — a crashlooping container, or an app deregistered
# mid-deploy so the target namespace is gone — re-ran its 600s poll forever,
# leaving the DeployAppWorkflow stuck `running` and saturating the worker's
# sync thread pool (starving unrelated short activities).
#
# Transient steps get a few backed-off attempts; the rollout poll fails
# terminally after its own 600s window (retrying just re-burns the worker).
_STANDARD_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
_ROLLOUT_RETRY = RetryPolicy(maximum_attempts=1)
_MARK_TIMEOUT = timedelta(minutes=2)


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

        try:
            await workflow.execute_activity(
                pre_flight, deployment_id, start_to_close_timeout=_TIMEOUT, retry_policy=_STANDARD_RETRY
            )
            await workflow.execute_activity(
                mark_deploying, deployment_id, start_to_close_timeout=_TIMEOUT, retry_policy=_STANDARD_RETRY
            )

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
                retry_policy=_STANDARD_RETRY,
            )
            if build_strategy != "off":
                _image_tags: dict = input.image_tags or {}
                _image_tag = next(iter(_image_tags.values()), "")
                await workflow.execute_activity(
                    build_image,
                    args=[BuildImageInput(
                        deployment_id=input.deployment_id,
                        image_tag=_image_tag,
                        commit_sha=input.commit_sha,
                    )],
                    # Builds are the longest step — a cold dockerfile build
                    # can run 10+ minutes. Give it a wide window and a
                    # heartbeat timeout (the kaniko driver heartbeats per
                    # poll) so a slow build isn't mistaken for a stuck one.
                    start_to_close_timeout=_BUILD_TIMEOUT,
                    heartbeat_timeout=timedelta(minutes=2),
                    retry_policy=_ROLLOUT_RETRY,
                )

            # provision_namespace runs *before* render so the namespace
            # exists when apply_manifests creates Services/Secrets in it.
            await workflow.execute_activity(
                provision_namespace,
                args=[input.registered_app_id, input.app_environment_id],
                start_to_close_timeout=_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
            # Static-site custom-domain TLS (#1010): CloudFront aliases need a
            # us-east-1 ACM cert (the regional ALB cert won't do). Ensure a
            # us-east-1 wildcard cert for the env's managed domain BEFORE the
            # cdn provisions, so the distribution comes up with the alias+cert.
            # Idempotent + reused across static apps; no-op when none are
            # public-static or there's no managed domain (serves on cloudfront.net).
            await workflow.execute_activity(
                ensure_cloudfront_cert,
                deployment_id,
                # Self-bounded internal cert poll (~6m) < start_to_close; no
                # heartbeat_timeout (the sync poll can't heartbeat from its thread).
                start_to_close_timeout=_BUILD_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
            # Static-site topology (#1010): a static_site workload implies an
            # (object_store bucket, cdn distribution) pair of managed services.
            # Ensure them — bucket first, then cdn — BEFORE ensure_workload_identity
            # so their iam_grants fold into the app runtime role. No-op fast-return
            # when the manifest has no static workload.
            await workflow.execute_activity(
                ensure_static_site_services,
                deployment_id,
                start_to_close_timeout=_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
            # Workload identity (#1011): create/update the app's IRSA role + bind
            # it to the ServiceAccount before render+apply, so the annotated SA
            # the render emits points at a role that already exists with the
            # right OIDC trust. No-op for apps with no IAM-authed managed service.
            await workflow.execute_activity(
                ensure_workload_identity,
                args=[input.registered_app_id, input.app_environment_id],
                start_to_close_timeout=_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
            await workflow.execute_activity(
                render_manifests, deployment_id, start_to_close_timeout=_TIMEOUT, retry_policy=_STANDARD_RETRY
            )
            await workflow.execute_activity(
                apply_manifests, deployment_id, start_to_close_timeout=_TIMEOUT, retry_policy=_STANDARD_RETRY
            )
            await workflow.execute_activity(
                update_secrets, deployment_id, start_to_close_timeout=_TIMEOUT, retry_policy=_STANDARD_RETRY
            )

            # Static-site asset pipeline (#1010): PLATFORM_BUILD dispatches an
            # in-cluster build/sync Job (terminal — it polls internally like
            # build_image); CI_PUSHED is a no-op + best-effort cache invalidate.
            # No-op fast-return when the manifest has no static workload.
            await workflow.execute_activity(
                sync_static_assets,
                args=[SyncStaticAssetsInput(deployment_id=deployment_id, commit_sha=input.commit_sha)],
                start_to_close_timeout=_BUILD_TIMEOUT,
                heartbeat_timeout=timedelta(minutes=2),
                retry_policy=_ROLLOUT_RETRY,
            )
            # Static sites have no Ingress, so external-dns never sees them.
            # Write the CNAME host -> CloudFront domain explicitly per public
            # static workload (idempotent UPSERT). No-op when none are static.
            await workflow.execute_activity(
                ensure_static_dns,
                deployment_id,
                start_to_close_timeout=_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )

            await workflow.execute_activity(
                wait_dns, deployment_id, start_to_close_timeout=_TIMEOUT, retry_policy=_STANDARD_RETRY
            )

            if self._abort_requested:
                return WorkflowResult(ok=False, message="aborted by signal")

            # Rollout poll + health check are terminal on failure: the driver
            # already polls ~600s internally, so a failure here means the
            # workload won't become healthy by re-running the deploy. One
            # attempt, then fall through to mark_failed (#1004).
            await workflow.execute_activity(
                poll_rollout, deployment_id, start_to_close_timeout=_TIMEOUT, retry_policy=_ROLLOUT_RETRY
            )
            await workflow.execute_activity(
                health_check, deployment_id, start_to_close_timeout=_TIMEOUT, retry_policy=_ROLLOUT_RETRY
            )
            await workflow.execute_activity(
                mark_running, deployment_id, start_to_close_timeout=_TIMEOUT, retry_policy=_STANDARD_RETRY
            )

            return WorkflowResult(ok=True, message="deployed")
        except ActivityError as exc:
            # #1004: a deploy activity that exhausts its bounded retries
            # (most often a rollout that timed out, or a crashlooping /
            # deregistered-mid-deploy workload) is terminal. Mark the
            # deployment failed and exit cleanly — never loop a poll
            # forever and saturate the worker.
            await workflow.execute_activity(
                mark_failed,
                args=[deployment_id, str(exc)],
                start_to_close_timeout=_MARK_TIMEOUT,
                retry_policy=_STANDARD_RETRY,
            )
            return WorkflowResult(ok=False, message=f"deploy failed: {exc}")
