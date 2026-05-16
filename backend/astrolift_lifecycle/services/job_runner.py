"""Ad-hoc Kubernetes Job spawned from a manifest-declared CronJob (#390).

Operator-facing "run scheduled job now" path. The workload's CronJob
template lives in ``astrolift.toml`` (parser produces a
``WorkloadManifest`` with ``kind="cronjob"`` and a ``schedule`` cron
string); for a one-shot run we don't want to alter the recurrence,
just dispatch a single Job from the same pod spec.

Why a direct apply rather than a Temporal workflow:

* The action is single-RTT: render → apply → record. A workflow would
  add a row in the runs table and a control-plane round trip without
  improving the operator UX (failures already surface synchronously
  through the MutationResult envelope).
* The platform's CronJob controller already owns the recurring path;
  the manual one-shot is intentionally a separate code path so a
  retried run can't accidentally interleave with the scheduled run
  (k8s Job names are unique, the short-uuid suffix guarantees this).

The rendered Job inherits the CronJob's ``jobTemplate.spec``
verbatim — same pod spec, same env, same image — and pins
``backoffLimit: 0`` so the manual run is observably one-shot regardless
of the cronjob's default retry policy.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from typing import Any

from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_lifecycle.models.jobs import ScheduledJobRun
from astrolift_registry.models import RegisteredApp, Workload
from core.app_deploy import (
    AppDeployError,
    driver_for_deployment,
    render_resources_for_deployment,
)

log = logging.getLogger(__name__)


class JobRunError(Exception):
    """Structured failure surfaced through the mutation envelope.

    ``code`` is one of ``ErrorCode``'s string values ("VALIDATION",
    "PRECONDITION", "NOT_FOUND", "INTERNAL") so the resolver maps it
    verbatim without translating exception classes."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclasses.dataclass(slots=True)
class JobRunResult:
    """What the mutation hands back on success.

    ``logs_url`` points the FE at the existing scheduled-job-runs
    surface (``/apps/<slug>/jobs``); the per-run logs come up there
    once the cluster reports the Job's status. ``run_name`` is the k8s
    Job name we applied — operators copy that into ``kubectl logs job/<name>``
    when troubleshooting outside the UI."""

    ok: bool
    run_name: str
    namespace: str
    logs_url: str | None = None
    error: str | None = None


def _resolve_env(app: RegisteredApp, environment_name: str) -> AppEnvironment:
    env = (
        AppEnvironment.objects.filter(
            registered_app=app,
            name=environment_name,
            deleted_at__isnull=True,
        )
        .select_related("tenant_cluster", "registered_app")
        .first()
    )
    if env is None:
        raise JobRunError(
            "NOT_FOUND",
            f"environment {environment_name!r} not found on app {app.slug!r}",
        )
    return env


def _latest_running_deployment(app: RegisteredApp, env: AppEnvironment) -> Deployment:
    deployment = (
        Deployment.objects.filter(
            registered_app=app,
            app_environment=env,
            status=Deployment.Status.RUNNING.value,
            deleted_at__isnull=True,
        )
        .order_by("-created_at")
        .first()
    )
    if deployment is None:
        raise JobRunError(
            "PRECONDITION",
            f"app {app.slug!r} environment {env.name!r} has no successful "
            "deployment yet — deploy the app at least once before running a job",
        )
    return deployment


def _cronjob_resource_for(
    resources: list[dict[str, Any]],
    job_slug: str,
) -> dict[str, Any]:
    for r in resources:
        if r.get("kind") != "CronJob":
            continue
        meta = r.get("metadata") or {}
        if meta.get("name") == job_slug:
            return r
    raise JobRunError(
        "VALIDATION",
        f"job {job_slug!r} not found among the app's cronjob workloads",
    )


def _build_job_from_cronjob(
    cronjob: dict[str, Any],
    *,
    run_name: str,
    namespace: str,
    actor_display: str,
) -> dict[str, Any]:
    """Lift the ``jobTemplate.spec`` out of a rendered CronJob into a
    standalone Job. Same pod spec, no schedule. ``backoffLimit: 0``
    pins the manual run as observably one-shot regardless of the
    cronjob's default retry policy."""
    spec = ((cronjob.get("spec") or {}).get("jobTemplate") or {}).get("spec") or {}
    template = spec.get("template") or {}
    base_labels: dict[str, str] = dict((cronjob.get("metadata") or {}).get("labels") or {})
    # Mark the manual one-shot so a list query can distinguish it from
    # controller-spawned Jobs without joining back through the platform
    # row. Kept verbose so it's obvious in ``kubectl describe job``.
    annotations = {
        "astrolift.io/trigger-kind": "manual",
        "astrolift.io/triggered-by": actor_display,
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": run_name,
            "namespace": namespace,
            "labels": {
                **base_labels,
                "astrolift.io/job-trigger": "manual",
            },
            "annotations": annotations,
        },
        "spec": {
            "backoffLimit": 0,
            "template": template,
        },
    }


def run_job_once(
    app: RegisteredApp,
    environment_name: str,
    job_slug: str,
    *,
    actor_user_id: int | None = None,
    actor_display: str = "",
) -> JobRunResult:
    """Render + apply a one-shot Job built from ``job_slug``'s CronJob.

    Validations (deny-by-default order):

    1. The app's normalized manifest carries a workload named
       ``job_slug`` with ``kind="cronjob"``. Anything else is
       ``VALIDATION``.
    2. The environment exists and resolves to a tenant cluster. Missing
       env is ``NOT_FOUND``.
    3. The app has a ``Deployment`` in ``RUNNING`` state for the env.
       Otherwise ``PRECONDITION`` — running a Job before the first
       successful deploy would land in an unprovisioned namespace with
       no image pulled.

    On apply failure, the driver's error chain surfaces in
    ``result.error`` with ``ok=False``; the caller maps that to
    ``MutationResult.errors`` with code ``INTERNAL``."""
    workload = Workload.objects.filter(
        registered_app=app,
        slug=job_slug,
        kind=Workload.Kind.CRONJOB,
        deleted_at__isnull=True,
    ).first()
    if workload is None:
        raise JobRunError(
            "VALIDATION",
            f"job {job_slug!r} not found among the app's cronjob workloads",
        )

    env = _resolve_env(app, environment_name)
    deployment = _latest_running_deployment(app, env)

    try:
        resources = render_resources_for_deployment(deployment)
    except AppDeployError as exc:
        raise JobRunError("PRECONDITION", str(exc)) from exc

    cronjob = _cronjob_resource_for(resources, job_slug)

    namespace = (cronjob.get("metadata") or {}).get("namespace") or ""
    if not namespace:
        raise JobRunError(
            "PRECONDITION",
            f"rendered CronJob/{job_slug} has no namespace — manifest is malformed",
        )

    run_name = f"{job_slug}-manual-{uuid.uuid4().hex[:8]}"
    job_resource = _build_job_from_cronjob(
        cronjob,
        run_name=run_name,
        namespace=namespace,
        actor_display=actor_display or "system",
    )

    try:
        driver, ctx, _ = driver_for_deployment(deployment)
    except AppDeployError as exc:
        raise JobRunError("PRECONDITION", str(exc)) from exc

    apply_result = driver.apply_manifests(ctx.slug, namespace, [job_resource])
    if not getattr(apply_result, "ok", False):
        error_msg = "; ".join(getattr(apply_result, "errors", ()) or ()) or "apply failed"
        log.warning(
            "run_job_once apply failed app=%s job=%s ns=%s err=%s",
            app.slug,
            job_slug,
            namespace,
            error_msg,
        )
        return JobRunResult(
            ok=False,
            run_name=run_name,
            namespace=namespace,
            logs_url=None,
            error=error_msg,
        )

    ScheduledJobRun.objects.create(
        workload=workload,
        app_environment=env,
        k8s_job_name=run_name,
        namespace=namespace,
        status=ScheduledJobRun.Status.RUNNING,
        trigger_kind=ScheduledJobRun.TriggerKind.MANUAL,
        triggered_by_id=actor_user_id,
    )

    logs_url = f"/apps/{app.slug}/jobs"
    log.info(
        "run_job_once applied app=%s job=%s ns=%s name=%s",
        app.slug,
        job_slug,
        namespace,
        run_name,
    )
    return JobRunResult(
        ok=True,
        run_name=run_name,
        namespace=namespace,
        logs_url=logs_url,
        error=None,
    )


__all__ = [
    "JobRunError",
    "JobRunResult",
    "run_job_once",
]
