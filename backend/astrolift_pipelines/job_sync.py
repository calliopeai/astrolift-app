"""Turning a parsed astrolift.toml into Job and Step rows (#1531).

Nothing in the tree created a `Job` row before this module existed.
`_mark_pipeline_run_running_sync` queried for them, got an empty list, fanned
out nothing, and marked the run SUCCESS. Every pipeline reported green
because it had nothing to run.

Two shapes, both legitimate and wanted for different reasons:

SNAPSHOT  The rows belong to the `PipelineRun`. A run keeps the definition
          it actually ran, so its job list is still true a year later.

SHARED    The rows belong to the `Pipeline` and are rewritten in place when
          the TOML changes. A pipeline with fifty jobs costs fifty rows
          however many times it runs.

SNAPSHOT is the default because SHARED has a tear in it. The fan-out reads
the job list once when the run starts, but each job is looked up again when
it spawns; under SHARED a definition edit between those two moments means
one run executes jobs from two different commits, with no record that it
did. Under SNAPSHOT the rows a run begins with are the rows it ends with.
SHARED remains available for organizations that would rather bound the row
count, which is a real cost at high run volume.

The mode is read from the organization. A pipeline has no cluster to carry
it: a CI-only pipeline never deploys anywhere, and one that does reaches a
cluster per deploy step rather than as a property of itself.
"""

from __future__ import annotations

from astrolift_ci_convert.common.types import JobDef, PipelineDef

SNAPSHOT = "snapshot"
SHARED = "shared"


def mode_for(pipeline_run) -> str:
    """Which shape this run's definition takes."""
    org = pipeline_run.pipeline.organization
    return SHARED if getattr(org, "pipeline_definition_mode", SNAPSHOT) == SHARED else SNAPSHOT


def sync_definition(pipeline_run, definition: PipelineDef) -> list:
    """Write `definition` into Job/Step rows for `pipeline_run`.

    Returns the jobs in the order the definition declared them. Callers
    should not query `Job` themselves — `jobs_for_run` and `job_for_run`
    exist so the mode is resolved in one place.
    """
    if mode_for(pipeline_run) == SNAPSHOT:
        return _write_snapshot(pipeline_run, definition)
    return _rewrite_shared(pipeline_run.pipeline, definition)


def jobs_for_run(pipeline_run):
    """The jobs this run should fan out over."""
    from astrolift_pipelines.models import Job

    if mode_for(pipeline_run) == SNAPSHOT:
        return Job.objects.filter(pipeline_run=pipeline_run, deleted_at__isnull=True)
    return Job.objects.filter(
        pipeline=pipeline_run.pipeline,
        pipeline_run__isnull=True,
        deleted_at__isnull=True,
    )


def job_for_run(pipeline_run, job_id: str):
    """One job by its id, from whichever set this run is using.

    Filtering by pipeline alone would raise MultipleObjectsReturned the
    moment a second run snapshotted the same job id, which is why the two
    call sites in `pipeline_job_spawn` both come through here.
    """
    return jobs_for_run(pipeline_run).get(job_id=job_id)


# ---------------------------------------------------------------------------


def _write_snapshot(pipeline_run, definition: PipelineDef) -> list:
    """Create this run's rows.

    Re-syncing an already-synced run replaces its rows rather than
    duplicating them: a Temporal activity can be retried, and a retry must
    not double the fan-out.
    """
    from astrolift_pipelines.models import Job

    for stale in Job.objects.filter(pipeline_run=pipeline_run, deleted_at__isnull=True):
        _soft_delete_job(stale)

    return [
        _create_job(definition_job, pipeline=pipeline_run.pipeline, pipeline_run=pipeline_run)
        for definition_job in definition.jobs
    ]


def _rewrite_shared(pipeline, definition: PipelineDef) -> list:
    """Bring the pipeline's living rows in line with the definition.

    Jobs are matched by `job_id` so an unchanged job keeps its primary key,
    and anything holding a reference to it (a JobRun from an earlier run)
    keeps pointing at the same row rather than at a tombstone.
    """
    from astrolift_pipelines.models import Job

    existing = {
        job.job_id: job
        for job in Job.objects.filter(
            pipeline=pipeline,
            pipeline_run__isnull=True,
            deleted_at__isnull=True,
        )
    }
    declared = {job.job_id for job in definition.jobs}

    for job_id, job in existing.items():
        if job_id not in declared:
            _soft_delete_job(job)

    out = []
    for definition_job in definition.jobs:
        job = existing.get(definition_job.job_id)
        if job is None:
            out.append(_create_job(definition_job, pipeline=pipeline, pipeline_run=None))
            continue
        job.name = definition_job.name
        job.runs_on = definition_job.runs_on or ""
        job.container_image = definition_job.container or ""
        job.needs = list(definition_job.needs)
        job.save(
            update_fields=[
                "name",
                "runs_on",
                "container_image",
                "needs",
                "updated_at",
                "version",
            ]
        )
        # Steps are replaced wholesale. They have no identity of their own
        # beyond position, so matching them up would be guesswork.
        _replace_steps(job, definition_job)
        out.append(job)
    return out


def _create_job(definition_job: JobDef, *, pipeline, pipeline_run):
    from astrolift_pipelines.models import Job

    job = Job.objects.create(
        pipeline=pipeline,
        pipeline_run=pipeline_run,
        job_id=definition_job.job_id,
        name=definition_job.name,
        runs_on=definition_job.runs_on or "",
        container_image=definition_job.container or "",
        needs=list(definition_job.needs),
    )
    _replace_steps(job, definition_job)
    return job


def _replace_steps(job, definition_job: JobDef) -> None:
    from astrolift_pipelines.models import Step

    for stale in Step.objects.filter(job=job, deleted_at__isnull=True):
        stale.soft_delete()

    Step.objects.bulk_create(
        [
            Step(
                job=job,
                position=position,
                step_id=definition_step.name or "",
                uses=definition_step.uses or None,
                run=definition_step.run or None,
                env=dict(definition_step.env),
                with_params=dict(definition_step.with_params),
            )
            for position, definition_step in enumerate(definition_job.steps)
        ]
    )


def _soft_delete_job(job) -> None:
    from astrolift_pipelines.models import Step

    for step in Step.objects.filter(job=job, deleted_at__isnull=True):
        step.soft_delete()
    job.soft_delete()
