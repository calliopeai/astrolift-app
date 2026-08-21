"""
Temporal activities for pipeline job K8s spawning and lifecycle (#69).

Activities:

  mark_pipeline_run_running(pipeline_run_id) -> dict
    Transitions PipelineRun → running, returns job metadata needed by
    the orchestrating workflow for topological sort.

  mark_pipeline_run_success(pipeline_run_id) -> None
    Transitions PipelineRun → success.

  mark_pipeline_run_failed(pipeline_run_id, reason) -> None
    Transitions PipelineRun → failure.

  spawn_pipeline_job(pipeline_run_id, job_id_str) -> int
    Creates a JobRun row, renders a batch/v1 Job manifest, applies it
    to the tenant cluster in the pipeline namespace. Returns job_run.pk.

  poll_pipeline_job(job_run_id) -> dict
    Polls the K8s Job status. Returns {"completed": bool, "failed": bool,
    "exit_code": int | None}. Updates JobRun status on completion.

  cancel_pipeline_job(job_run_id) -> None
    Deletes the K8s Job (best-effort) and marks the JobRun cancelled.

  mark_job_run_cancelled(job_run_id) -> None
    Marks a pending/running JobRun as cancelled (no K8s op).

  mark_job_run_failed(job_run_id, exit_code) -> None
    Marks a JobRun as failed with optional exit_code.

Namespace isolation:
  Pipeline jobs run in ``astrolift-pipelines-<org-slug>``, separate from
  tenant app namespaces. The spawner creates the namespace when absent and
  applies a ResourceQuota from org-level defaults.

Secret injection:
  Secrets declared on the Job row are mounted as a K8s Secret; they are
  not surfaced in the Job spec's env array so they don't appear in K8s
  audit events.
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.pipeline_job_spawn")

# Pipeline jobs run in a dedicated namespace, separate from app namespaces.
_PIPELINE_NS_PREFIX = "astrolift-pipelines-"

# How much of a pipeline pod's output is kept. The pod is deleted with its
# Job, so this excerpt is the only record of what the build printed; the
# caps keep a chatty build from turning a JobRun row into a liability.
PIPELINE_LOG_LINES = 500
PIPELINE_LOG_CHARS = 64_000

# Resource quota applied to every pipeline namespace.
_DEFAULT_RESOURCE_QUOTA = {
    "requests.cpu": "8",
    "requests.memory": "16Gi",
    "limits.cpu": "16",
    "limits.memory": "32Gi",
}


# ---------------------------------------------------------------------------
# Sync helpers (Django-touching, called via sync_to_async)
# ---------------------------------------------------------------------------


def _mark_pipeline_run_running_sync(pipeline_run_id: int) -> dict:
    from django.utils import timezone

    from astrolift_pipelines.models import Job, PipelineRun

    run = PipelineRun.objects.select_related("pipeline__organization").get(pk=pipeline_run_id)
    run.status = PipelineRun.Status.RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at", "updated_at", "version"])

    jobs = list(
        Job.objects.filter(
            pipeline=run.pipeline,
            deleted_at__isnull=True,
        ).values("id", "job_id", "name", "container_image", "runs_on", "needs")
    )

    return {"jobs": jobs}


def _mark_pipeline_run_success_sync(pipeline_run_id: int) -> None:
    from django.utils import timezone

    from astrolift_pipelines.models import PipelineRun

    run = PipelineRun.objects.get(pk=pipeline_run_id)
    run.status = PipelineRun.Status.SUCCESS
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "finished_at", "updated_at", "version"])


def _mark_pipeline_run_failed_sync(pipeline_run_id: int, reason: str) -> None:
    from django.utils import timezone

    from astrolift_pipelines.models import PipelineRun

    run = PipelineRun.objects.get(pk=pipeline_run_id)
    run.status = PipelineRun.Status.FAILURE
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "finished_at", "updated_at", "version"])
    log.info(
        "pipeline_run_failed id=%s reason=%s",
        pipeline_run_id,
        reason,
    )


def _spawn_pipeline_job_sync(pipeline_run_id: int, job_id_str: str) -> int:
    """Create a JobRun row, render the K8s Job manifest, and apply it."""
    from django.utils import timezone

    from astrolift_pipelines.models import Job, JobRun, PipelineRun, Step, StepRun
    from astrolift_pipelines.step_script import render_step_script

    run = PipelineRun.objects.select_related(
        "pipeline__organization",
    ).get(pk=pipeline_run_id)
    job = Job.objects.get(pipeline=run.pipeline, job_id=job_id_str, deleted_at__isnull=True)

    steps = list(Step.objects.filter(job=job, deleted_at__isnull=True).order_by("position"))
    script = render_step_script(steps)

    job_run = JobRun.objects.create(
        pipeline_run=run,
        job=job,
        status=JobRun.Status.RUNNING,
        started_at=timezone.now(),
    )

    # One StepRun per Step, up front. Nothing created these before, so
    # `AstroliftJobRun.stepRuns` was an empty list on every run and the
    # per-step columns held their defaults forever (#1501). Created at
    # spawn rather than as each step starts, because the control plane
    # does not watch the pod step by step — it settles them from the
    # termination message once the Job is terminal.
    StepRun.objects.bulk_create(
        [StepRun(job_run=job_run, step=step, status=StepRun.Status.PENDING) for step in steps]
    )

    org_slug = run.pipeline.organization.slug
    namespace = _pipeline_namespace(org_slug)
    k8s_job_name = _k8s_job_name(run, job)

    try:
        client = _get_cluster_client(run)
        _ensure_pipeline_namespace(client, namespace, org_slug)
        manifest = _build_job_manifest(k8s_job_name, namespace, job, run, script)
        client.server_side_apply(manifest, field_manager="astrolift-pipelines")
    except Exception as exc:
        job_run.status = JobRun.Status.FAILURE
        job_run.finished_at = timezone.now()
        job_run.save(update_fields=["status", "finished_at", "updated_at", "version"])
        raise RuntimeError(f"spawn_pipeline_job failed for {job_id_str!r}: {exc}") from exc

    job_run.temporal_activity_id = k8s_job_name
    job_run.save(update_fields=["temporal_activity_id", "updated_at", "version"])

    log.info(
        "spawned k8s Job name=%s namespace=%s job_run_id=%s",
        k8s_job_name,
        namespace,
        job_run.pk,
    )
    return job_run.pk


def _poll_pipeline_job_sync(job_run_id: int) -> dict:
    """Poll K8s Job status for a running JobRun.

    Returns ``{"completed": bool, "failed": bool, "exit_code": int|None}``.
    Marks the JobRun SUCCESS when the K8s Job completes cleanly.
    """
    from django.utils import timezone

    from astrolift_pipelines.models import JobRun

    job_run = JobRun.objects.select_related(
        "job",
        "pipeline_run__pipeline__organization",
    ).get(pk=job_run_id)

    # Already terminal — return cached result.
    if job_run.status == JobRun.Status.SUCCESS:
        return {"completed": True, "failed": False, "exit_code": 0}
    if job_run.status in (JobRun.Status.FAILURE, JobRun.Status.CANCELLED):
        return {"completed": False, "failed": True, "exit_code": None}

    run = job_run.pipeline_run
    org_slug = run.pipeline.organization.slug
    k8s_job_name = job_run.temporal_activity_id
    namespace = _pipeline_namespace(org_slug)

    try:
        client = _get_cluster_client(run)
        status = client.get(kind="Job", namespace=namespace, name=k8s_job_name)
        job_status = status.get("status") or {}
    except Exception as exc:  # noqa: BLE001 — treat fetch failures as transient
        log.warning("poll_pipeline_job: get failed job_run_id=%s: %s", job_run_id, exc)
        return {"completed": False, "failed": False, "exit_code": None}

    succeeded = int(job_status.get("succeeded") or 0)
    failed_count = int(job_status.get("failed") or 0)
    active = int(job_status.get("active") or 0)
    conditions = job_status.get("conditions") or []

    complete = any(c.get("type") == "Complete" and c.get("status") == "True" for c in conditions)
    job_failed = any(c.get("type") == "Failed" and c.get("status") == "True" for c in conditions)

    if complete or succeeded > 0:
        # Everything the pod can tell us has to be read here: the delete
        # below is `propagation_policy="Foreground"`, so the pod and its
        # logs go with the Job.
        pod = _read_job_pod(client, namespace, k8s_job_name)
        _settle_step_runs(job_run=job_run, pod=pod, job_failed=False)
        _capture_job_logs(job_run=job_run, run=run, pod=pod)
        job_run.status = JobRun.Status.SUCCESS
        job_run.finished_at = timezone.now()
        job_run.save(update_fields=["status", "finished_at", "updated_at", "version"])
        # Delete the K8s Job after success — keeps the pipeline namespace tidy.
        _delete_k8s_job(client, namespace, k8s_job_name)
        return {"completed": True, "failed": False, "exit_code": 0}

    if job_failed or (failed_count > 0 and active == 0):
        pod = _read_job_pod(client, namespace, k8s_job_name)
        settled = _settle_step_runs(job_run=job_run, pod=pod, job_failed=True)
        # A failed build is the case an operator actually goes looking at,
        # so the logs matter more here than on the green path.
        _capture_job_logs(job_run=job_run, run=run, pod=pod)
        # The pod's own exit code beats parsing it out of a Job condition
        # message, which is what `_extract_exit_code` has to do and which
        # had never run against a real failure while the container was a
        # stub that always exited 0.
        exit_code = settled if settled is not None else _extract_exit_code(conditions)
        job_run.status = JobRun.Status.FAILURE
        job_run.finished_at = timezone.now()
        job_run.save(update_fields=["status", "finished_at", "updated_at", "version"])
        _delete_k8s_job(client, namespace, k8s_job_name)
        return {"completed": False, "failed": True, "exit_code": exit_code}

    # Still running.
    return {"completed": False, "failed": False, "exit_code": None}


def _settle_step_runs(
    *,
    job_run: Any,
    pod: dict | None,
    job_failed: bool,
) -> int | None:
    """Settle this job run's StepRun rows from the pod's exit records.

    Returns the failing step's exit code when there is one, so the caller
    can report the container's own code rather than parsing it out of a
    Job condition message.

    Never raises. Step-level detail is diagnostics; failing to read it
    must not change the job's own verdict, which the Job status already
    decided. A run whose records cannot be read keeps whatever it can
    infer from that verdict.
    """
    from django.utils import timezone

    from astrolift_pipelines.models import StepRun
    from astrolift_pipelines.step_script import STATUS_SKIPPED, parse_step_records

    step_runs = list(
        StepRun.objects.filter(job_run=job_run).select_related("step").order_by("step__position")
    )
    if not step_runs:
        return None

    records = parse_step_records(_terminated_message(pod))

    now = timezone.now()
    failing_exit: int | None = None
    seen_failure = False

    for step_run in step_runs:
        position = int(getattr(step_run.step, "position", 0) or 0)
        record = records.get(position)

        if record is None:
            # No record. Either the step never ran because an earlier one
            # failed, or the records were unreadable — in which case the
            # Job's own verdict is all there is.
            if seen_failure or job_failed:
                step_run.status = StepRun.Status.SKIPPED if seen_failure else StepRun.Status.FAILURE
            else:
                step_run.status = StepRun.Status.SUCCESS
        elif record[0] == STATUS_SKIPPED:
            # A `uses:` step. Recorded rather than passed over silently,
            # so the run says plainly that a declared step did nothing.
            step_run.status = StepRun.Status.SKIPPED
        elif record[1] == 0:
            step_run.status = StepRun.Status.SUCCESS
            step_run.exit_code = 0
        else:
            step_run.status = StepRun.Status.FAILURE
            step_run.exit_code = record[1]
            if not seen_failure:
                failing_exit = record[1]
            seen_failure = True

        step_run.finished_at = now
        step_run.save(update_fields=["status", "exit_code", "finished_at", "updated_at", "version"])

    return failing_exit


def _read_job_pod(client: Any, namespace: str, k8s_job_name: str) -> dict | None:
    """:func:`_job_pod`, but a cluster that will not answer is not fatal.

    Both callers are settling a run whose verdict the Job status already
    decided. Losing the pod costs detail, never the verdict.
    """
    try:
        return _job_pod(client, namespace, k8s_job_name)
    except Exception:  # noqa: BLE001 — k8s client raises many subtypes
        log.warning("read_job_pod: could not list pods for %s", k8s_job_name, exc_info=True)
        return None


def _job_pod(client: Any, namespace: str, k8s_job_name: str) -> dict | None:
    """This Job's pod.

    `backoffLimit: 0` and `restartPolicy: Never`, so there is one pod per
    Job. It is found by the label the pod template already carries rather
    than by name, which the Job generates. The namespace is shared by every
    pipeline the org runs, so taking the first pod would read another job's
    results.
    """
    for pod in client.list(kind="Pod", namespace=namespace) or []:
        labels = ((pod.get("metadata") or {}).get("labels")) or {}
        if labels.get("job-name") == k8s_job_name:
            return pod
    return None


def _terminated_message(pod: dict | None) -> str:
    """The `main` container's termination message, or ``""``."""
    for status in (((pod or {}).get("status") or {}).get("containerStatuses")) or []:
        if status.get("name") != "main":
            continue
        terminated = (status.get("state") or {}).get("terminated") or {}
        return str(terminated.get("message") or "")
    return ""


def _capture_job_logs(*, job_run: Any, run: Any, pod: dict | None) -> None:
    """Store the tail of the pod's output on the JobRun.

    Called before ``_delete_k8s_job``, which is the only window there is:
    the delete is ``propagation_policy="Foreground"``, so the pod goes with
    the Job and its logs go with the pod. ``ttlSecondsAfterFinished`` never
    gets a chance to matter.

    Never raises. A log read is diagnostics, and diagnostics failing must
    not change a run's verdict — which the Job status has already decided
    by the time this runs.
    """
    pod_name = ((pod or {}).get("metadata") or {}).get("name") or ""
    if not pod_name:
        return

    from asgiref.sync import async_to_sync

    from core.cluster_observability import fetch_pod_log_tail

    try:
        cluster = _resolve_cluster(run)
        lines = async_to_sync(fetch_pod_log_tail)(
            cluster=cluster,
            namespace=_pipeline_namespace(run.pipeline.organization.slug),
            pod_name=pod_name,
            tail=PIPELINE_LOG_LINES,
        )
    except Exception:  # noqa: BLE001 — the read must not cost us the run
        log.warning("capture_job_logs: could not read logs for pod %s", pod_name, exc_info=True)
        return

    body = "\n".join(line for line in lines if line and line.strip())
    if not body:
        return
    if len(body) > PIPELINE_LOG_CHARS:
        body = "…" + body[-PIPELINE_LOG_CHARS:]

    job_run.log_excerpt = body
    job_run.save(update_fields=["log_excerpt", "updated_at", "version"])


def _cancel_pipeline_job_sync(job_run_id: int) -> None:
    from django.utils import timezone

    from astrolift_pipelines.models import JobRun

    job_run = JobRun.objects.select_related(
        "pipeline_run__pipeline__organization",
    ).get(pk=job_run_id)

    if job_run.status in (
        JobRun.Status.CANCELLED,
        JobRun.Status.SUCCESS,
        JobRun.Status.FAILURE,
    ):
        return

    run = job_run.pipeline_run
    org_slug = run.pipeline.organization.slug
    k8s_job_name = job_run.temporal_activity_id
    namespace = _pipeline_namespace(org_slug)

    try:
        client = _get_cluster_client(run)
        _delete_k8s_job(client, namespace, k8s_job_name)
    except Exception:  # noqa: BLE001 — best-effort
        pass

    job_run.status = JobRun.Status.CANCELLED
    job_run.finished_at = timezone.now()
    job_run.save(update_fields=["status", "finished_at", "updated_at", "version"])


def _mark_job_run_cancelled_sync(job_run_id: int) -> None:
    from django.utils import timezone

    from astrolift_pipelines.models import JobRun

    job_run = JobRun.objects.get(pk=job_run_id)
    if job_run.status in (
        JobRun.Status.SUCCESS,
        JobRun.Status.FAILURE,
        JobRun.Status.CANCELLED,
    ):
        return
    job_run.status = JobRun.Status.CANCELLED
    job_run.finished_at = timezone.now()
    job_run.save(update_fields=["status", "finished_at", "updated_at", "version"])


def _mark_job_run_failed_sync(job_run_id: int, exit_code: int | None) -> None:
    from django.utils import timezone

    from astrolift_pipelines.models import JobRun

    job_run = JobRun.objects.get(pk=job_run_id)
    if job_run.status in (JobRun.Status.SUCCESS, JobRun.Status.FAILURE):
        return
    job_run.status = JobRun.Status.FAILURE
    job_run.finished_at = timezone.now()
    job_run.save(update_fields=["status", "finished_at", "updated_at", "version"])


# ---------------------------------------------------------------------------
# Private K8s helpers (no Django imports)
# ---------------------------------------------------------------------------


def _pipeline_namespace(org_slug: str) -> str:
    return f"{_PIPELINE_NS_PREFIX}{org_slug}"


def _k8s_job_name(run: Any, job: Any) -> str:
    """Deterministic Job name: ``pl-{run_pk}-{job_id}`` truncated to 63 chars."""
    raw = f"pl-{run.pk}-{job.job_id}"
    return raw[:63].rstrip("-")


def _resolve_cluster(run: Any) -> Any:
    """The org cluster a pipeline run's jobs land on.

    Pipeline jobs run on the org's default cluster (the first active cluster
    bound to the organization). The spawner does NOT require a specific
    AppEnvironment cluster binding — pipelines are org-scoped, not app-scoped.

    Split out from :func:`_get_cluster_client` because the log capture needs
    the ``TenantCluster`` itself, not the dynamic client:
    ``fetch_pod_log_tail`` speaks the observability layer's driver lookup.
    """
    from astrolift_clusters.models import TenantCluster

    org = run.pipeline.organization
    cluster = (
        TenantCluster.objects.filter(
            organization=org,
            deleted_at__isnull=True,
            lifecycle_state="managed",
        )
        .order_by("created_at")
        .first()
    )
    if cluster is None:
        raise RuntimeError(
            f"organization {org.slug!r} has no managed cluster — pipeline jobs cannot be scheduled"
        )
    return cluster


def _get_cluster_client(run: Any) -> Any:
    """Resolve the KubernetesDynamicClient for the pipeline run's org cluster."""

    from astrolift_drivers.registry import plugins
    from core.cluster_observability import _config_for  # type: ignore[attr-defined]

    cluster = _resolve_cluster(run)

    plugin = plugins.get(cluster.provider_plugin_id)
    if plugin is None:
        raise RuntimeError(
            f"cluster {cluster.slug!r} plugin not loaded — cannot build K8s client for pipeline job spawn"
        )

    config = _config_for(cluster)
    driver = plugin.cluster_driver(config)
    # Use the driver's internal dynamic client if exposed; otherwise
    # build one via the cluster context.
    if hasattr(driver, "_k8s"):
        return driver._k8s
    raise RuntimeError(
        f"cluster driver for {cluster.slug!r} does not expose a K8s dynamic client — "
        f"pipeline job spawning requires a direct-apply cluster driver"
    )


def _ensure_pipeline_namespace(client: Any, namespace: str, org_slug: str) -> None:
    """Create the pipeline namespace and apply a ResourceQuota if absent."""
    try:
        client.get(kind="Namespace", name=namespace, namespace="")
    except Exception:  # noqa: BLE001 — NotFoundError or similar
        ns_manifest = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": namespace,
                "labels": {
                    "astrolift.io/managed-by": "astrolift",
                    "astrolift.io/pipeline-namespace": "true",
                    "astrolift.io/org-slug": org_slug,
                },
            },
        }
        client.server_side_apply(ns_manifest, field_manager="astrolift-pipelines")

        quota_manifest = {
            "apiVersion": "v1",
            "kind": "ResourceQuota",
            "metadata": {
                "name": "astrolift-pipeline-quota",
                "namespace": namespace,
            },
            "spec": {"hard": _DEFAULT_RESOURCE_QUOTA},
        }
        client.server_side_apply(quota_manifest, field_manager="astrolift-pipelines")


def _build_job_manifest(
    k8s_job_name: str,
    namespace: str,
    job: Any,
    run: Any,
    script: str,
) -> dict:
    """Render a batch/v1 Job manifest for a pipeline Job row.

    Security constraints enforced at spawn time (not relying on TOML):
      - No privileged containers.
      - No hostNetwork / hostPID.
      - readOnlyRootFilesystem not forced (container may need temp writes).
      - allowPrivilegeEscalation=false.
      - runAsNonRoot=true.
    """
    from astrolift_pipelines.step_script import TERMINATION_LOG

    image = (job.container_image or "").strip() or "ubuntu:22.04"

    container: dict = {
        "name": "main",
        "image": image,
        # The job's own steps (#1501). This was
        # `echo 'pipeline job started'; exit 0` — unconditional — so every
        # pipeline reported SUCCESS without running any of the operator's
        # steps.
        "command": ["/bin/sh", "-c", script],
        # File, so the kubelet reads the per-step records the script
        # appends. Pod logs are not an option: poll_pipeline_job deletes
        # the Job on both terminal branches and the pod goes with it
        # (#1218).
        "terminationMessagePath": TERMINATION_LOG,
        "terminationMessagePolicy": "File",
        "securityContext": {
            "privileged": False,
            "allowPrivilegeEscalation": False,
            "runAsNonRoot": True,
        },
        "env": [
            {"name": "ASTROLIFT_PIPELINE_RUN_ID", "value": str(run.pk)},
            {"name": "ASTROLIFT_JOB_ID", "value": str(job.job_id)},
        ],
    }

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": k8s_job_name,
            "namespace": namespace,
            "labels": {
                "astrolift.io/managed-by": "astrolift",
                "astrolift.io/pipeline-run-id": str(run.pk),
                "astrolift.io/job-id": job.job_id[:63],
            },
        },
        "spec": {
            "ttlSecondsAfterFinished": 300,
            "backoffLimit": 0,
            "template": {
                "metadata": {
                    "labels": {
                        "astrolift.io/pipeline-run-id": str(run.pk),
                        "astrolift.io/job-id": job.job_id[:63],
                    }
                },
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "hostNetwork": False,
                    "hostPID": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                    },
                    "containers": [container],
                },
            },
        },
    }


def _delete_k8s_job(client: Any, namespace: str, name: str) -> None:
    """Best-effort delete of a K8s Job + dependent pods."""
    try:
        client.delete(
            kind="Job",
            namespace=namespace,
            name=name,
            propagation_policy="Foreground",
        )
    except Exception:  # noqa: BLE001 — already gone or auth denied
        pass


def _extract_exit_code(conditions: list[dict]) -> int | None:
    """Extract the exit code from K8s Job conditions when available."""
    for cond in conditions:
        msg = cond.get("message") or ""
        if "exit code" in msg.lower():
            parts = msg.split()
            for i, p in enumerate(parts):
                if "exit" in p.lower() and i + 1 < len(parts):
                    try:
                        return int(parts[i + 1].rstrip("."))
                    except (ValueError, IndexError):
                        pass
    return None


# ---------------------------------------------------------------------------
# Temporal activity definitions
# ---------------------------------------------------------------------------


@activity.defn(name="astrolift.pipeline.mark_run_running")
async def mark_pipeline_run_running(pipeline_run_id: int) -> dict:
    """Transition PipelineRun → running; return job metadata for DAG sort."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_mark_pipeline_run_running_sync)(pipeline_run_id)


@activity.defn(name="astrolift.pipeline.mark_run_success")
async def mark_pipeline_run_success(pipeline_run_id: int) -> None:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_pipeline_run_success_sync)(pipeline_run_id)


@activity.defn(name="astrolift.pipeline.mark_run_failed")
async def mark_pipeline_run_failed(pipeline_run_id: int, reason: str = "") -> None:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_pipeline_run_failed_sync)(pipeline_run_id, reason)


@activity.defn(name="astrolift.pipeline.spawn_job")
async def spawn_pipeline_job(pipeline_run_id: int, job_id_str: str) -> int:
    """Create a JobRun and schedule a K8s Job for a single pipeline job.

    Returns the JobRun pk used by subsequent poll/cancel activities.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_spawn_pipeline_job_sync)(pipeline_run_id, job_id_str)


@activity.defn(name="astrolift.pipeline.poll_job")
async def poll_pipeline_job(job_run_id: int) -> dict:
    """Poll the K8s Job status for a running JobRun.

    Returns ``{"completed": bool, "failed": bool, "exit_code": int|None}``.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_poll_pipeline_job_sync)(job_run_id)


@activity.defn(name="astrolift.pipeline.cancel_job")
async def cancel_pipeline_job(job_run_id: int) -> None:
    """Delete the K8s Job and mark the JobRun cancelled."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_cancel_pipeline_job_sync)(job_run_id)


@activity.defn(name="astrolift.pipeline.mark_job_run_cancelled")
async def mark_job_run_cancelled(job_run_id: int) -> None:
    """Mark a pending/running JobRun cancelled without a K8s call."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_job_run_cancelled_sync)(job_run_id)


@activity.defn(name="astrolift.pipeline.mark_job_run_failed")
async def mark_job_run_failed(job_run_id: int, exit_code: int | None = None) -> None:
    """Mark a JobRun as failed with an optional exit code."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_mark_job_run_failed_sync)(job_run_id, exit_code)
