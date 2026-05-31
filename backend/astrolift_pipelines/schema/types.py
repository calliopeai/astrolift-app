"""GraphQL types for Pipeline, PipelineRun, Job, JobRun, Step, StepRun, Trigger, Artifact."""

from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import GUID

JSON = strawberry.scalars.JSON


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
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    created_at: dt.datetime


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
    step_runs = list(jr.step_runs.select_related("step", "step__job").order_by("step__position"))
    return JobRunType(
        id=GUID(str(jr.guid)),
        job=job_to_type(jr.job),
        status=jr.status,
        step_runs=[step_run_to_type(sr) for sr in step_runs],
        started_at=jr.started_at,
        finished_at=jr.finished_at,
        created_at=jr.created_at,
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
    job_runs = list(
        pr.job_runs.select_related("job", "job__pipeline")
        .prefetch_related("step_runs__step")
        .order_by("job__job_id")
    )
    return PipelineRunType(
        id=GUID(str(pr.guid)),
        run_number=pr.run_number,
        trigger_kind=pr.trigger_kind,
        trigger_ref=pr.trigger_ref or "",
        trigger_actor=pr.trigger_actor or "",
        status=pr.status,
        job_runs=[job_run_to_type(jr) for jr in job_runs],
        started_at=pr.started_at,
        finished_at=pr.finished_at,
        created_at=pr.created_at,
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
    )
