"""Run a one-shot command in a cluster, as Temporal activities (#1600).

`astrolift_workflows.command_run` has carried the policy since the feature
was specced -- timeout normalisation, the Job spec plan, failure-mode
classification, and the concurrency guard -- and had no caller, because
neither workflow it was written for existed. The models (`CommandRun`,
`ScheduledJobRun`), their admin, and their GraphQL types all shipped too.

These are the activities the workflows drive. They stay thin on purpose:
every decision (how long, what the Job looks like, what a failure means)
already lives in `command_run`, and duplicating any of it here would give
the tree two answers to the same question.
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio import activity

log = logging.getLogger(__name__)

#: Excerpt kept on the row. The full log lives in the cluster until the
#: Job's ttlSecondsAfterFinished expires; this is what an operator sees
#: without leaving the UI.
LOG_EXCERPT_LIMIT = 4000


def _render_job_manifest(plan) -> dict[str, Any]:
    """`JobSpecPlan` -> a batch/v1 Job.

    Kept next to the activity rather than in `command_run` because that
    module is deliberately free of cluster shapes -- its docstring says the
    caller renders the manifest so its tests do not need a cluster.
    """
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": plan.job_name,
            "namespace": plan.namespace,
            "labels": {"astrolift.io/managed-by": "astrolift", "astrolift.io/kind": "command-run"},
        },
        "spec": {
            "backoffLimit": plan.backoff_limit,
            "activeDeadlineSeconds": plan.timeout_seconds,
            "ttlSecondsAfterFinished": plan.cleanup_after_seconds,
            "template": {
                "metadata": {"labels": {"astrolift.io/kind": "command-run"}},
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "command",
                            "image": plan.image,
                            "command": list(plan.command),
                            "env": [{"name": k, "value": v} for k, v in sorted(plan.env.items())],
                        }
                    ],
                },
            },
        },
    }


def _start_sync(command_run_id: int) -> dict[str, Any]:
    from django.utils import timezone

    from astrolift_lifecycle.models import CommandRun
    from astrolift_workflows.command_run import plan_job_spec

    run = CommandRun.all_objects.select_related("registered_app").get(pk=command_run_id)
    app = run.registered_app

    from core.app_deploy import namespace_for_app
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    cluster = _cluster_for(run)
    plan = plan_job_spec(
        app_slug=app.slug,
        run_id=str(run.guid)[:8],
        namespace=namespace_for_app(app),
        image=_image_for(run, app),
        command=list(run.command or []),
        env={},
        timeout_seconds=getattr(run, "timeout_seconds", None),
    )

    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    result = driver.apply_manifests(ctx.slug, plan.namespace, [_render_job_manifest(plan)])
    if not result.ok:
        raise RuntimeError(
            f"command run {run.guid}: could not create Job {plan.job_name!r}: "
            + "; ".join(str(e) for e in result.errors),
        )

    run.k8s_job_name = plan.job_name
    run.namespace = plan.namespace
    run.started_at = timezone.now()
    run.save(update_fields=["k8s_job_name", "namespace", "started_at", "updated_at", "version"])
    return {"job_name": plan.job_name, "namespace": plan.namespace, "timeout": plan.timeout_seconds}


def _cluster_for(run):
    """The cluster this run's app is bound to."""
    from core.app_deploy import AppDeployError

    env = getattr(run, "app_environment", None) or getattr(run.registered_app, "default_environment", None)
    cluster = getattr(env, "tenant_cluster", None) or getattr(
        run.registered_app, "default_tenant_cluster", None
    )
    if cluster is None:
        raise AppDeployError(
            f"command run {run.guid}: app {run.registered_app.slug!r} has no cluster to run on",
        )
    return cluster


def _image_for(run, app) -> str:
    """The image the command runs in.

    The app's own image, deliberately: a command run is "run this in my
    app's environment", so a different image would have different
    dependencies and a different filesystem, and the output would not mean
    what the operator thinks it means.
    """
    workload = getattr(run, "workload", None)
    image = getattr(workload, "image", "") or getattr(app, "registry_repo_uri", "") or ""
    if not image:
        raise RuntimeError(f"command run {run.guid}: no image to run the command in")
    return image


def _poll_sync(command_run_id: int) -> dict[str, Any]:
    from astrolift_lifecycle.models import CommandRun
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    run = CommandRun.all_objects.select_related("registered_app").get(pk=command_run_id)
    driver = _driver_for_cluster(_cluster_for(run))
    ctx = _context_for_cluster(_cluster_for(run))
    status = driver.read_job_status(ctx, namespace=run.namespace, job_name=run.k8s_job_name)

    # A Job whose pod has not scheduled yet reads as all-zeroes, which is
    # "nothing determined yet" rather than failure -- the JobStatus docstring
    # is explicit about that, and treating it as failure would fail every run
    # that takes a moment to schedule.
    if status.succeeded:
        return {"state": "succeeded", "conditions": list(status.conditions)}
    if status.failed:
        return {"state": "failed", "conditions": list(status.conditions)}
    return {"state": "running", "conditions": list(status.conditions)}


def _finish_sync(command_run_id: int, outcome: dict[str, Any]) -> dict[str, Any]:
    from django.utils import timezone

    from astrolift_lifecycle.models import CommandRun

    run = CommandRun.all_objects.get(pk=command_run_id)
    state = str(outcome.get("state", "failed"))

    run.ended_at = timezone.now()
    run.exit_code = 0 if state == "succeeded" else 1
    excerpt = "\n".join(outcome.get("conditions") or [])
    if excerpt:
        run.log_excerpt = excerpt[:LOG_EXCERPT_LIMIT]
    run.save(update_fields=["ended_at", "exit_code", "log_excerpt", "updated_at", "version"])
    return {"state": state, "exit_code": run.exit_code}


@activity.defn(name="astrolift.command_run.start")
async def start_command_run(command_run_id: int) -> dict[str, Any]:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_start_sync)(command_run_id)


@activity.defn(name="astrolift.command_run.poll")
async def poll_command_run(command_run_id: int) -> dict[str, Any]:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_poll_sync)(command_run_id)


@activity.defn(name="astrolift.command_run.finish")
async def finish_command_run(command_run_id: int, outcome: dict[str, Any]) -> dict[str, Any]:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_finish_sync)(command_run_id, outcome)


def _assert_no_concurrent_sync(payload: dict[str, Any]) -> dict[str, Any]:
    """Refuse a "Run Now" while another is in flight (spec 06 §4.20a).

    Reads the in-flight rows and hands them to
    ``command_run.assert_no_concurrent_run``, which owns the rule. The query
    is here because that module is deliberately ORM-free; the decision is
    not.
    """
    from astrolift_lifecycle.models import ScheduledJobRun as ScheduledJobRunRow
    from astrolift_workflows.command_run import (
        CommandRunStatus,
        ScheduledJobRun,
        assert_no_concurrent_run,
    )

    workload_id = payload.get("workload_id")
    rows = ScheduledJobRunRow.objects.filter(
        workload_id=workload_id,
        status=ScheduledJobRunRow.Status.RUNNING,
        deleted_at__isnull=True,
    ).exclude(pk=payload.get("scheduled_job_run_id"))

    assert_no_concurrent_run(
        cron_job_name=str(payload.get("cron_job_name") or workload_id or "?"),
        in_flight_runs=[
            ScheduledJobRun(
                run_id=str(r.guid),
                status=CommandRunStatus.RUNNING,
                started_at_unix=int(r.started_at.timestamp()) if r.started_at else 0,
            )
            for r in rows
        ],
        allow_concurrent=bool(payload.get("allow_concurrent", False)),
    )
    return {"ok": True}


@activity.defn(name="astrolift.command_run.assert_no_concurrent")
async def assert_no_concurrent(payload: dict[str, Any]) -> dict[str, Any]:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_assert_no_concurrent_sync)(payload)
