"""Dispatch a ``kind: task`` workload's run as a Kubernetes Job (#2330).

``runTask`` records a ``TaskRun`` and calls :func:`dispatch_task_run`. The
Job is the task's own rendered ``batch/v1 Job`` from the environment's latest
running deployment (same image, env, resources and GPU scheduling), renamed
per run so runs never collide, with the reviewed command override applied
to the primary container. The run reconciler takes it from there.

The Job name is derived from the run's GUID, so re-dispatching the same run
re-applies the same Job instead of starting another.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from django.utils import timezone

from astrolift_lifecycle.models import Deployment, TaskRun
from core.app_deploy import AppDeployError, driver_for_deployment, render_resources_for_deployment

log = logging.getLogger(__name__)

_JOB_NAME_MAX = 63


def task_job_name(run: TaskRun) -> str:
    suffix = f"-run-{str(run.guid).replace('-', '')[-10:]}"
    return f"{run.workload.slug[: _JOB_NAME_MAX - len(suffix)].rstrip('-')}{suffix}"


def _latest_running_deployment(run: TaskRun) -> Deployment | None:
    return (
        Deployment.objects.filter(
            registered_app=run.workload.registered_app,
            app_environment=run.app_environment,
            status=Deployment.Status.RUNNING.value,
            deleted_at__isnull=True,
        )
        .order_by("-created_at")
        .first()
    )


def _task_job(resources: list[dict[str, Any]], slug: str) -> dict[str, Any] | None:
    for resource in resources:
        if resource.get("kind") == "Job" and (resource.get("metadata") or {}).get("name") == slug:
            return resource
    return None


def build_run_job(rendered: dict[str, Any], run: TaskRun) -> dict[str, Any]:
    """The per-run Job: the rendered task Job, renamed, labelled and with the
    command override on the primary (first) container."""
    job = copy.deepcopy(rendered)
    meta = job.setdefault("metadata", {})
    meta["name"] = task_job_name(run)
    meta.setdefault("labels", {})["astrolift.io/task-run"] = str(run.guid)
    meta.setdefault("annotations", {})["astrolift.io/trigger-kind"] = run.trigger_kind
    spec = job.setdefault("spec", {})
    spec["backoffLimit"] = 0
    template = spec.setdefault("template", {})
    template.setdefault("metadata", {}).setdefault("labels", {})["astrolift.io/task-run"] = str(run.guid)
    if run.command:
        containers = template.setdefault("spec", {}).get("containers") or []
        if containers:
            containers[0]["command"] = list(run.command)
            containers[0].pop("args", None)
    return job


def _fail(run: TaskRun, reason: str) -> TaskRun:
    run.status = TaskRun.Status.FAILED
    run.failure_reason = reason[:4000]
    run.ended_at = timezone.now()
    run.save(update_fields=["status", "failure_reason", "ended_at", "updated_at", "version"])
    log.warning("task run %s dispatch failed: %s", run.guid, reason)
    return run


def dispatch_task_run(run: TaskRun) -> TaskRun:
    """Apply ``run``'s Job, or record why it could not be applied."""
    if run.app_environment_id is None:
        return _fail(run, "no target environment")
    deployment = _latest_running_deployment(run)
    if deployment is None:
        return _fail(
            run,
            f"environment {run.app_environment.name!r} has no running deployment; deploy the app before running tasks",
        )
    try:
        resources = render_resources_for_deployment(deployment)
        driver, ctx, _ = driver_for_deployment(deployment)
    except AppDeployError as exc:
        return _fail(run, str(exc))
    rendered = _task_job(resources, run.workload.slug)
    if rendered is None:
        return _fail(run, f"task {run.workload.slug!r} is not in the deployed manifest; redeploy the app")
    job = build_run_job(rendered, run)
    namespace = (job.get("metadata") or {}).get("namespace") or ""
    if not namespace:
        return _fail(run, "rendered task Job has no namespace")
    try:
        result = driver.apply_manifests(ctx.slug, namespace, [job])
    except Exception as exc:  # noqa: BLE001 - recorded on the run, never raised to the caller
        return _fail(run, f"applying the Job failed: {exc}")
    if not getattr(result, "ok", False):
        return _fail(run, "; ".join(getattr(result, "errors", ()) or ()) or "applying the Job failed")
    run.k8s_job_name = job["metadata"]["name"]
    run.namespace = namespace
    run.save(update_fields=["k8s_job_name", "namespace", "updated_at", "version"])
    return run
