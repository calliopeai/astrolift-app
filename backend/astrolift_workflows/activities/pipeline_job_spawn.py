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

import hashlib
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

    from astrolift_ci_convert.common.toml_reader import TomlReadError, read_toml
    from astrolift_pipelines.job_sync import jobs_for_run, sync_definition
    from astrolift_pipelines.models import PipelineRun
    from astrolift_pipelines.toml_fetcher import TomlFetchError, fetch_pipeline_toml

    run = PipelineRun.objects.select_related("pipeline__organization").get(pk=pipeline_run_id)
    run.status = PipelineRun.Status.RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at", "updated_at", "version"])
    # Post "pending" as soon as the run starts. A branch-protection rule
    # requiring astrolift/{pipeline} needs the check to *exist* before it
    # can be satisfied; without this the PR waits on a check that never
    # appears.
    _post_commit_status(run)

    # Load the definition the run is actually of. Until #1531 nothing did
    # this: the fetcher had no callers, there was no parser to hand it to,
    # and no code path created a Job row. The query below always came back
    # empty and the workflow marked the run SUCCESS for having nothing to
    # do.
    try:
        toml_text = fetch_pipeline_toml(run)
        definition = read_toml(toml_text)
        sync_definition(run, definition)
        # Stamp what was executed, not what was asked for. `trigger_ref` is a
        # branch, and a branch moves; the digest is the only thing that can
        # answer "which document did THIS run behave according to" once the
        # ref has advanced. Recorded after the sync so a run only claims a
        # definition it managed to persist.
        run.definition_digest = hashlib.sha256(toml_text.encode("utf-8")).hexdigest()
        run.definition_schema_version = definition.schema_version
        run.save(
            update_fields=[
                "definition_digest",
                "definition_schema_version",
                "updated_at",
                "version",
            ]
        )
    except (TomlFetchError, TomlReadError) as exc:
        # Distinct from "the definition declares no jobs", which is a
        # legitimately empty run. Returning an empty job list here would
        # report a pipeline whose TOML could not be read as green.
        return {"jobs": [], "error": str(exc)}

    jobs = list(jobs_for_run(run).values("id", "job_id", "name", "container_image", "runs_on", "needs"))

    return {"jobs": jobs}


# Prometheus series for the pipeline subsystem (#98).
#
# astrolift_pipelines/metrics.py registered the counters and histograms on
# prometheus_client's default registry -- the one config.views.metrics_view
# serves via generate_latest() -- and then nothing ever called the helpers,
# so /metrics carried zero pipeline series. Every terminal transition in this
# module now records, which is why these wrappers exist rather than a call
# inlined once: there are six job-run terminal paths and two run-level ones,
# and the failure mode of adding a seventh without a metric is exactly what
# left this module dark in the first place. test_pipeline_metrics_wired.py
# pins that as a ratchet.
#
# Metrics must never fail a pipeline: a labelling bug or a registry problem
# would otherwise turn a green deploy red, so both wrappers swallow.


def _record_job_metric(job_run, *, backend: str = "k8s_job") -> None:
    from astrolift_pipelines.metrics import record_job_completed

    try:
        record_job_completed(job_run, backend=backend)
    except Exception:  # noqa: BLE001 - observability must not break the run
        log.warning("pipeline metrics: job_run=%s not recorded", job_run.pk, exc_info=True)


def _record_run_metric(run) -> None:
    from astrolift_pipelines.metrics import record_run_completed

    try:
        record_run_completed(run)
    except Exception:  # noqa: BLE001
        log.warning("pipeline metrics: pipeline_run=%s not recorded", run.pk, exc_info=True)


def _post_commit_status(run) -> None:
    """Reflect the run's current status onto the commit that triggered it.

    Separate from the metric wrapper even though both are fire-and-forget:
    a metric is local and cannot fail slowly, while this is an outbound
    HTTP call to a host that may be down. The wrapper it calls swallows,
    and this swallows again, because a status transition must land in the
    database whether or not GitHub answers.
    """
    from astrolift_pipelines.commit_status import post_commit_status_for_run

    post_commit_status_for_run(run)


def _mark_pipeline_run_success_sync(pipeline_run_id: int) -> None:
    from django.utils import timezone

    from astrolift_pipelines.models import PipelineRun

    run = PipelineRun.objects.get(pk=pipeline_run_id)
    run.status = PipelineRun.Status.SUCCESS
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "finished_at", "updated_at", "version"])
    _record_run_metric(run)
    _post_commit_status(run)


def _mark_pipeline_run_failed_sync(pipeline_run_id: int, reason: str) -> None:
    from django.utils import timezone

    from astrolift_pipelines.models import PipelineRun

    run = PipelineRun.objects.get(pk=pipeline_run_id)
    run.status = PipelineRun.Status.FAILURE
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "finished_at", "updated_at", "version"])
    _record_run_metric(run)
    _post_commit_status(run)
    log.info(
        "pipeline_run_failed id=%s reason=%s",
        pipeline_run_id,
        reason,
    )


def _spawn_pipeline_job_sync(pipeline_run_id: int, job_id_str: str) -> int:
    """Create a JobRun row, render the K8s Job manifest, and apply it."""
    from django.utils import timezone

    from astrolift_pipelines.job_sync import job_for_run
    from astrolift_pipelines.models import JobRun, PipelineRun, Step, StepRun
    from astrolift_pipelines.secret_plumbing import (
        make_env_from_refs,
        materialize_job_secrets,
        resolve_pipeline_secrets,
        secret_names_for_job,
    )
    from astrolift_pipelines.step_script import render_step_script

    run = PipelineRun.objects.select_related(
        "pipeline__organization",
    ).get(pk=pipeline_run_id)
    # Not a plain Job lookup by pipeline: under snapshot mode every run
    # holds its own row for this job_id, and filtering by pipeline alone
    # would raise MultipleObjectsReturned on the second run.
    job = job_for_run(run, job_id_str)

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

    # The secrets this job's definition refers to. Until #1529 the whole
    # module had no caller: `resolve_pipeline_secrets` took a name list
    # nothing could construct, so a job needing a registry credential or a
    # deploy key could not express it at all.
    secret_names = secret_names_for_job(job, steps)

    # A pull request from a fork runs code nobody in the org has reviewed,
    # in a job that would otherwise be handed the org's registry
    # credentials and deploy keys. The decision was made at the receiver,
    # where the payload was, and is only read here (#1529, #95).
    if secret_names and getattr(run, "skip_secrets", False):
        log.info(
            "spawn_pipeline_job: withholding %d secret(s) from fork run %s",
            len(secret_names),
            run.pk,
        )
        secret_names = []

    try:
        client = _get_cluster_client(run, job)
        _ensure_pipeline_namespace(client, namespace, org_slug)

        secret_env: list[dict] = []
        if secret_names:
            # Resolution raises on a name the org store does not hold, so a
            # typo fails the job here with the name in the message rather
            # than inside the container as an unresolved reference.
            bundle = resolve_pipeline_secrets(run, secret_names)
            k8s_secret = materialize_job_secrets(job_run, bundle, namespace=namespace, cluster=client)
            secret_env = make_env_from_refs(k8s_secret, sorted(bundle))

        manifest = _build_job_manifest(k8s_job_name, namespace, job, run, script, secret_env)
        client.server_side_apply(manifest, field_manager="astrolift-pipelines")
    except Exception as exc:
        job_run.status = JobRun.Status.FAILURE
        job_run.finished_at = timezone.now()
        job_run.save(update_fields=["status", "finished_at", "updated_at", "version"])
        _record_job_metric(job_run)
        # A spawn that dies after materializing leaves a plaintext
        # credential in the namespace with no pod that needs it and no
        # poll that will ever clean up, since poll only runs for a job
        # that started.
        if secret_names:
            _cleanup_secrets_quietly(job_run, namespace, run)
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
        _record_job_metric(job_run)
        # Delete the K8s Job after success — keeps the pipeline namespace tidy.
        _delete_k8s_job(client, namespace, k8s_job_name)
        _cleanup_secrets_quietly(job_run, namespace, run, client=client)
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
        _record_job_metric(job_run)
        _delete_k8s_job(client, namespace, k8s_job_name)
        # Beside the Job delete on this branch too: a per-run Secret that
        # outlives the pod is a plaintext credential sitting in a
        # namespace, and the failure path is the one that gets forgotten.
        _cleanup_secrets_quietly(job_run, namespace, run, client=client)
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
        # Routed by the job, not the org default: the pod ran on whichever
        # cluster its `runs_on` selected, and reading logs from a different
        # one would come back empty rather than wrong-looking.
        cluster = _resolve_cluster(run, job_run.job)
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

    # Redact before truncating and before storing. #1218 built this capture
    # deliberately ahead of #1529's secrets, because capture without
    # secrets is safe and secrets without redaction are not: the first
    # mounted credential would otherwise land in a stored excerpt.
    redacted = _redact_secrets(job_run=job_run, run=run, body=body)
    if redacted is None:
        # The job uses secrets and we could not resolve them to mask them.
        # Storing the raw output would be the leak this exists to prevent,
        # so the excerpt is dropped instead. Diagnostics lose; the run does
        # not change.
        log.warning(
            "capture_job_logs: skipping excerpt for job_run=%s — secrets unresolvable to redact",
            job_run.pk,
        )
        return
    body = redacted

    if len(body) > PIPELINE_LOG_CHARS:
        body = "…" + body[-PIPELINE_LOG_CHARS:]

    job_run.log_excerpt = body
    job_run.save(update_fields=["log_excerpt", "updated_at", "version"])


def _redact_secrets(*, job_run: Any, run: Any, body: str) -> str | None:
    """Mask this job's secret values in `body`.

    Returns the masked text, or None when the job uses secrets that could
    not be resolved — the caller drops the excerpt rather than store it
    unmasked.

    The values are re-resolved rather than carried from spawn: the bundle
    is plaintext, and persisting it anywhere so a later activity could read
    it back would be a worse leak than the one being prevented. A secret
    rotated between spawn and this call would be masked by its new value
    and not its old one, which is the one gap in this and is inherent to
    matching on values.
    """
    from astrolift_pipelines.models import Step
    from astrolift_pipelines.secret_plumbing import (
        build_redactor_for_job,
        resolve_pipeline_secrets,
        secret_names_for_job,
    )

    steps = list(Step.objects.filter(job=job_run.job, deleted_at__isnull=True).order_by("position"))
    names = secret_names_for_job(job_run.job, steps)
    if not names:
        return body
    try:
        bundle = resolve_pipeline_secrets(run, names)
    except Exception:  # noqa: BLE001 — fail closed, see the docstring
        return None
    return build_redactor_for_job(bundle).redact_lines(body)


def _cleanup_secrets_quietly(job_run: Any, namespace: str, run: Any, *, client: Any = None) -> None:
    """Delete this job run's K8s Secret. Never raises.

    Secret cleanup must not change a run's verdict, which is already
    decided by the time this is called — but it must also not be skipped,
    because what is left behind is a plaintext credential in a namespace
    the pod that needed it has already left.
    """
    from astrolift_pipelines.secret_plumbing import cleanup_job_secrets

    try:
        cleanup_job_secrets(
            job_run,
            namespace=namespace,
            cluster=client if client is not None else _get_cluster_client(run),
        )
    except Exception:  # noqa: BLE001
        log.warning(
            "cleanup_job_secrets: left behind for job_run=%s in %s",
            job_run.pk,
            namespace,
            exc_info=True,
        )


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
        # The third terminal path. A cancelled job leaves exactly the same
        # plaintext credential behind as a failed one, and poll never runs
        # again to notice.
        _cleanup_secrets_quietly(job_run, namespace, run, client=client)
    except Exception:  # noqa: BLE001 — best-effort
        pass

    job_run.status = JobRun.Status.CANCELLED
    job_run.finished_at = timezone.now()
    job_run.save(update_fields=["status", "finished_at", "updated_at", "version"])
    _record_job_metric(job_run)


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
    _record_job_metric(job_run)


def _mark_job_run_failed_sync(job_run_id: int, exit_code: int | None) -> None:
    from django.utils import timezone

    from astrolift_pipelines.models import JobRun

    job_run = JobRun.objects.get(pk=job_run_id)
    if job_run.status in (JobRun.Status.SUCCESS, JobRun.Status.FAILURE):
        return
    job_run.status = JobRun.Status.FAILURE
    job_run.finished_at = timezone.now()
    job_run.save(update_fields=["status", "finished_at", "updated_at", "version"])
    _record_job_metric(job_run)


# ---------------------------------------------------------------------------
# Private K8s helpers (no Django imports)
# ---------------------------------------------------------------------------


def _pipeline_namespace(org_slug: str) -> str:
    return f"{_PIPELINE_NS_PREFIX}{org_slug}"


def _k8s_job_name(run: Any, job: Any) -> str:
    """Deterministic Job name: ``pl-{run_pk}-{job_id}`` truncated to 63 chars."""
    raw = f"pl-{run.pk}-{job.job_id}"
    return raw[:63].rstrip("-")


def _resolve_cluster(run: Any, job: Any = None) -> Any:
    """The cluster a pipeline job lands on, from its own ``runs_on``.

    Routing lives in :mod:`astrolift_pipelines.dispatch_router`, which had
    no caller. What used to be here took the org's oldest managed cluster
    and ignored ``runs_on`` entirely, so ``cluster:prod-eu``,
    ``["linux","arm64"]``, ``windows`` and ``self-hosted`` all landed on the
    same place — an arm64 job onto amd64 nodes, and a self-hosted job as a
    K8s Job on the platform's own cluster.

    It also could not run at all: it filtered on ``lifecycle_state``, which
    is not a field on ``TenantCluster`` (the column is ``lifecycle``), so
    every call raised ``FieldError``. Nothing caught it because every test
    of this path patches this function out.

    ``job`` is optional because the log capture calls this with only the
    run, after the job has already been placed; without it this resolves
    the org default, which is what that path needs.

    Split out from :func:`_get_cluster_client` because the log capture needs
    the ``TenantCluster`` itself, not the dynamic client:
    ``fetch_pod_log_tail`` speaks the observability layer's driver lookup.
    """
    from astrolift_pipelines.dispatch_router import route

    org = run.pipeline.organization
    runs_on = str(getattr(job, "runs_on", "") or "") if job is not None else ""

    decision = route(runs_on or "astrolift/default", org)

    if decision.runner_only:
        # A self-hosted or macOS job belongs to a registered runner agent,
        # which claims work over the runner API. Spawning it as a K8s Job
        # here would run it on the platform's cluster instead — the wrong
        # machine, quietly.
        raise RuntimeError(
            f"job requires a self-hosted runner ({runs_on!r}); "
            f"K8s spawn does not apply. {decision.reason}"
        )
    if decision.cluster is None:
        raise RuntimeError(
            f"organization {org.slug!r} has no cluster matching {runs_on or 'astrolift/default'!r} "
            f"— pipeline job cannot be scheduled. {decision.reason}"
        )
    return decision.cluster


def _get_cluster_client(run: Any, job: Any = None) -> Any:
    """Resolve the KubernetesDynamicClient for the pipeline run's org cluster."""

    from astrolift_drivers.registry import plugins
    from core.cluster_observability import _config_for  # type: ignore[attr-defined]

    cluster = _resolve_cluster(run, job)

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
    secret_env: list[dict] | None = None,
) -> dict:
    """Render a batch/v1 Job manifest for a pipeline Job row.

    Security constraints enforced at spawn time (not relying on TOML):
      - No privileged containers.
      - No hostNetwork / hostPID.
      - readOnlyRootFilesystem not forced (container may need temp writes).
      - allowPrivilegeEscalation=false.
      - runAsNonRoot=true.
    """
    from astrolift_pipelines.build_image import resolve_job_image
    from astrolift_pipelines.step_script import TERMINATION_LOG

    image = resolve_job_image(job, run)

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
            # secretKeyRef entries, never literal values: a value inlined
            # here would be readable from the Job spec by anyone who can
            # get the object, and would appear in K8s audit events (#1529).
            *(secret_env or []),
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
