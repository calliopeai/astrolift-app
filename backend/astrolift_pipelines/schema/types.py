"""GraphQL types for Pipeline, PipelineRun, Job, JobRun, Step, StepRun, Trigger, Artifact."""

from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import GUID

JSON = strawberry.scalars.JSON


@strawberry.type(name="AstroliftPipelineSecret")
class PipelineSecretType:
    """Secret metadata only; values and ciphertext have no GraphQL field."""

    id: GUID
    name: str
    created_at: dt.datetime
    updated_at: dt.datetime


@strawberry.type(name="AstroliftPipelineSecretChange")
class PipelineSecretChangeType:
    pipeline_id: GUID
    name: str


@strawberry.type(name="AstroliftTrigger")
class TriggerType:
    id: GUID
    pipeline_id: GUID
    kind: str
    config: JSON
    created_at: dt.datetime


@strawberry.type(name="AstroliftStep")
class StepType:
    id: GUID
    job_id: GUID
    position: int
    step_id: str
    uses: str | None
    run: str | None
    env: JSON
    with_params: JSON
    created_at: dt.datetime


@strawberry.type(name="AstroliftJob")
class JobType:
    id: GUID
    pipeline_id: GUID
    job_id: str
    name: str
    runs_on: str
    container_image: str
    needs: JSON
    created_at: dt.datetime


@strawberry.type(name="AstroliftStepRun")
class StepRunType:
    id: GUID
    step: StepType
    status: str
    exit_code: int | None
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    created_at: dt.datetime


@strawberry.type(name="AstroliftJobRun")
class JobRunType:
    id: GUID
    job: JobType
    status: str
    step_runs: list[StepRunType]
    # The tail of what the job's pod printed, captured before the K8s Job
    # is deleted (#1218). On the job rather than the step because a job is
    # one container: every step writes to the same stream. This is what
    # `astro pipeline logs` reads.
    log_excerpt: str
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    created_at: dt.datetime
    log_kind: str = "recorded"
    log_truncated: bool = False
    steps_truncated: bool = False
    cleanup_status: str = "unknown"
    cleanup_last_error: str | None = None


@strawberry.type(name="AstroliftArtifact")
class ArtifactType:
    id: GUID
    name: str
    size_bytes: int
    content_type: str
    download_url: str
    created_at: dt.datetime


@strawberry.type(name="AstroliftPipelineRun")
class PipelineRunType:
    id: GUID
    run_number: int
    trigger_kind: str
    trigger_ref: str
    trigger_actor: str
    status: str
    job_runs: list[JobRunType]
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    created_at: dt.datetime
    version: int = 0
    pipeline_id: GUID | None = None
    organization_id: GUID | None = None
    app_id: GUID | None = None
    pipeline_version: int = 0
    request_id: str | None = None
    temporal_workflow_id: str = ""
    temporal_run_id: str | None = None
    dispatch_status: str = "unknown"
    dispatch_last_error: str | None = None
    cancellation_status: str = "not_requested"
    cancellation_observed_at: dt.datetime | None = None
    cancellation_last_error: str | None = None
    cleanup_status: str = "unknown"
    jobs_truncated: bool = False


@strawberry.type(name="AstroliftRegisteredAppStub")
class RegisteredAppStubType:
    """Minimal registered-app projection surfaced on PipelineType.

    The full RegisteredApp type lives in astrolift_registry/schema/types.py.
    We emit a stub here to avoid a circular import between the two schema
    modules while still giving callers the fields they need to link back to
    the app (guid + name + slug).
    """

    id: GUID
    name: str
    slug: str


@strawberry.type(name="AstroliftPipeline")
class PipelineType:
    id: GUID
    name: str
    repo_url: str
    default_branch: str
    toml_path: str
    # The RegisteredApp this pipeline deploys, if any. None for pure-CI
    # pipelines that have no deploy step.
    astrolift_app: RegisteredAppStubType | None
    triggers: list[TriggerType]
    created_at: dt.datetime
    updated_at: dt.datetime
    version: int = 0
    organization_id: GUID | None = None


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def trigger_to_type(t) -> TriggerType:
    return TriggerType(
        id=GUID(str(t.guid)),
        pipeline_id=GUID(str(t.pipeline.guid)),
        kind=t.kind,
        config=t.config or {},
        created_at=t.created_at,
    )


def step_to_type(s) -> StepType:
    return StepType(
        id=GUID(str(s.guid)),
        job_id=GUID(str(s.job.guid)),
        position=s.position,
        step_id=s.step_id or "",
        uses=s.uses,
        run=s.run,
        env=s.env or {},
        with_params=s.with_params or {},
        created_at=s.created_at,
    )


def job_to_type(j) -> JobType:
    return JobType(
        id=GUID(str(j.guid)),
        pipeline_id=GUID(str(j.pipeline.guid)),
        job_id=j.job_id,
        name=j.name,
        runs_on=j.runs_on or "",
        container_image=j.container_image or "",
        needs=j.needs or [],
        created_at=j.created_at,
    )


def step_run_to_type(sr) -> StepRunType:
    return StepRunType(
        id=GUID(str(sr.guid)),
        step=step_to_type(sr.step),
        status=sr.status,
        exit_code=sr.exit_code,
        started_at=sr.started_at,
        finished_at=sr.finished_at,
        created_at=sr.created_at,
    )


def job_run_to_type(jr) -> JobRunType:
    step_runs = getattr(jr, "bounded_steps", None)
    if step_runs is None:
        step_runs = list(
            jr.step_runs.select_related("step", "step__job").order_by("step__position", "pk")[:51]
        )
    return JobRunType(
        id=GUID(str(jr.guid)),
        job=job_to_type(jr.job),
        status=jr.status,
        step_runs=[step_run_to_type(sr) for sr in step_runs[:50]],
        log_excerpt=(jr.log_excerpt or "")[-64000:],
        started_at=jr.started_at,
        finished_at=jr.finished_at,
        created_at=jr.created_at,
        log_truncated=len(jr.log_excerpt or "") > 64000,
        steps_truncated=len(step_runs) > 50,
        cleanup_status=jr.cleanup_status,
        cleanup_last_error=jr.cleanup_last_error or None,
    )


def artifact_to_type(a) -> ArtifactType:
    return ArtifactType(
        id=GUID(str(a.guid)),
        name=a.name,
        size_bytes=int(a.size_bytes),
        content_type=a.content_type or "",
        download_url="",
        created_at=a.created_at,
    )


def pipeline_run_to_type(pr) -> PipelineRunType:
    from django.db.models import Prefetch

    from astrolift_pipelines.models import StepRun

    job_runs = list(
        pr.job_runs.select_related("job", "job__pipeline")
        .prefetch_related(
            Prefetch(
                "step_runs",
                queryset=StepRun.objects.select_related("step", "step__job").order_by("step__position", "pk")[
                    :51
                ],
                to_attr="bounded_steps",
            )
        )
        .order_by("job__job_id", "pk")[:21]
    )
    return PipelineRunType(
        id=GUID(str(pr.guid)),
        run_number=pr.run_number,
        trigger_kind=pr.trigger_kind,
        trigger_ref=pr.trigger_ref or "",
        trigger_actor=pr.trigger_actor or "",
        status=pr.status,
        job_runs=[job_run_to_type(jr) for jr in job_runs[:20]],
        started_at=pr.started_at,
        finished_at=pr.finished_at,
        created_at=pr.created_at,
        version=pr.version,
        pipeline_id=GUID(str(pr.pipeline.guid)),
        organization_id=GUID(str(pr.organization.guid)) if pr.organization_id else None,
        app_id=GUID(str(pr.registered_app.guid)) if pr.registered_app_id else None,
        pipeline_version=pr.pipeline_version,
        request_id=pr.request_id,
        temporal_workflow_id=pr.temporal_workflow_id,
        temporal_run_id=pr.temporal_run_id or None,
        dispatch_status=pr.dispatch_status,
        dispatch_last_error=pr.dispatch_last_error or None,
        cancellation_status=pr.cancellation_status,
        cancellation_observed_at=pr.cancellation_observed_at,
        cancellation_last_error=pr.cancellation_last_error or None,
        cleanup_status=pr.cleanup_status,
        jobs_truncated=len(job_runs) > 20,
    )


def registered_app_stub_to_type(app) -> RegisteredAppStubType:
    return RegisteredAppStubType(
        id=GUID(str(app.guid)),
        name=app.name,
        slug=app.slug,
    )


def pipeline_to_type(p) -> PipelineType:
    triggers = list(p.triggers.filter(deleted_at__isnull=True).order_by("kind"))
    app_stub = registered_app_stub_to_type(p.registered_app) if p.registered_app_id else None
    return PipelineType(
        id=GUID(str(p.guid)),
        name=p.name,
        repo_url=p.repo_url or "",
        default_branch=p.default_branch or "main",
        toml_path=p.toml_path or "",
        astrolift_app=app_stub,
        triggers=[trigger_to_type(t) for t in triggers],
        created_at=p.created_at,
        updated_at=p.updated_at,
        version=p.version,
        organization_id=GUID(str(p.organization.guid)),
    )
