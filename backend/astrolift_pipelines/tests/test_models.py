"""Basic model tests for astrolift_pipelines.

Covers:
- Creating all eight models with valid FK chains
- __str__ returns a non-empty string for each
- soft_delete sets deleted_at; restore clears it
- SoftDeleteManager excludes deleted rows; all_objects returns them
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization
from astrolift_pipelines.models import (
    Artifact,
    Job,
    JobRun,
    Pipeline,
    PipelineRun,
    Step,
    StepRun,
    Trigger,
)

pytestmark = pytest.mark.django_db


@pytest.fixture()
def org():
    return Organization.objects.create(name="PipelinesOrg", slug="pipelines-org")


@pytest.fixture()
def pipeline(org):
    return Pipeline.objects.create(
        organization=org,
        name="my-pipeline",
        repo_url="https://github.com/acme/app",
        default_branch="main",
    )


@pytest.fixture()
def pipeline_run(pipeline):
    return PipelineRun.objects.create(
        pipeline=pipeline,
        run_number=1,
        trigger_kind=PipelineRun.TriggerKind.PUSH,
        trigger_ref="refs/heads/main",
        trigger_actor="bot",
        status=PipelineRun.Status.PENDING,
    )


@pytest.fixture()
def job(pipeline):
    return Job.objects.create(
        pipeline=pipeline,
        job_id="build",
        name="Build",
        runs_on="ubuntu-22.04",
    )


@pytest.fixture()
def job_run(pipeline_run, job):
    return JobRun.objects.create(
        pipeline_run=pipeline_run,
        job=job,
        status=JobRun.Status.PENDING,
    )


@pytest.fixture()
def step(job):
    return Step.objects.create(
        job=job,
        position=1,
        step_id="checkout",
        uses="actions/checkout@v4",
    )


@pytest.fixture()
def step_run(job_run, step):
    return StepRun.objects.create(
        job_run=job_run,
        step=step,
        status=StepRun.Status.PENDING,
    )


@pytest.fixture()
def artifact(pipeline_run, job_run):
    return Artifact.objects.create(
        pipeline_run=pipeline_run,
        job_run=job_run,
        name="dist.tar.gz",
        blob_key="runs/1/dist.tar.gz",
        size_bytes=1024,
        content_type="application/gzip",
    )


@pytest.fixture()
def trigger(pipeline):
    return Trigger.objects.create(
        pipeline=pipeline,
        kind=Trigger.Kind.PUSH,
        config={"branches": ["main"]},
    )


class TestPipeline:
    def test_create(self, pipeline):
        assert pipeline.pk is not None
        assert pipeline.guid is not None

    def test_str(self, pipeline):
        s = str(pipeline)
        assert "my-pipeline" in s

    def test_toml_path_auto_populated(self, pipeline):
        assert pipeline.toml_path == ".astrolift/pipelines/my-pipeline.toml"

    def test_soft_delete(self, pipeline):
        assert pipeline.deleted_at is None
        pipeline.soft_delete()
        assert pipeline.deleted_at is not None
        assert Pipeline.objects.filter(pk=pipeline.pk).count() == 0
        assert Pipeline.all_objects.filter(pk=pipeline.pk).count() == 1

    def test_restore(self, pipeline):
        pipeline.soft_delete()
        pipeline.restore()
        assert pipeline.deleted_at is None
        assert Pipeline.objects.filter(pk=pipeline.pk).count() == 1


class TestPipelineRun:
    def test_create(self, pipeline_run):
        assert pipeline_run.run_number == 1

    def test_str(self, pipeline_run):
        s = str(pipeline_run)
        assert "#1" in s

    def test_soft_delete(self, pipeline_run):
        pipeline_run.soft_delete()
        assert PipelineRun.objects.filter(pk=pipeline_run.pk).count() == 0
        assert PipelineRun.all_objects.filter(pk=pipeline_run.pk).count() == 1


class TestJob:
    def test_create(self, job):
        assert job.job_id == "build"

    def test_str(self, job):
        s = str(job)
        assert "build" in s

    def test_soft_delete(self, job):
        job.soft_delete()
        assert Job.objects.filter(pk=job.pk).count() == 0
        assert Job.all_objects.filter(pk=job.pk).count() == 1


class TestJobRun:
    def test_create(self, job_run):
        assert job_run.status == JobRun.Status.PENDING

    def test_str(self, job_run):
        assert "JobRun" in str(job_run) or "PipelineRun" in str(job_run)

    def test_soft_delete(self, job_run):
        job_run.soft_delete()
        assert JobRun.objects.filter(pk=job_run.pk).count() == 0
        assert JobRun.all_objects.filter(pk=job_run.pk).count() == 1


class TestStep:
    def test_create(self, step):
        assert step.position == 1
        assert step.uses == "actions/checkout@v4"

    def test_str(self, step):
        s = str(step)
        assert "checkout" in s or "Job" in s

    def test_soft_delete(self, step):
        step.soft_delete()
        assert Step.objects.filter(pk=step.pk).count() == 0
        assert Step.all_objects.filter(pk=step.pk).count() == 1


class TestStepRun:
    def test_create(self, step_run):
        assert step_run.status == StepRun.Status.PENDING
        assert step_run.exit_code is None

    def test_str(self, step_run):
        assert str(step_run) != ""

    def test_soft_delete(self, step_run):
        step_run.soft_delete()
        assert StepRun.objects.filter(pk=step_run.pk).count() == 0
        assert StepRun.all_objects.filter(pk=step_run.pk).count() == 1


class TestArtifact:
    def test_create(self, artifact):
        assert artifact.size_bytes == 1024
        assert artifact.blob_key == "runs/1/dist.tar.gz"

    def test_str(self, artifact):
        assert "dist.tar.gz" in str(artifact)

    def test_soft_delete(self, artifact):
        artifact.soft_delete()
        assert Artifact.objects.filter(pk=artifact.pk).count() == 0
        assert Artifact.all_objects.filter(pk=artifact.pk).count() == 1


class TestTrigger:
    def test_create(self, trigger):
        assert trigger.kind == Trigger.Kind.PUSH
        assert trigger.config == {"branches": ["main"]}

    def test_str(self, trigger):
        assert "push" in str(trigger)

    def test_soft_delete(self, trigger):
        trigger.soft_delete()
        assert Trigger.objects.filter(pk=trigger.pk).count() == 0
        assert Trigger.all_objects.filter(pk=trigger.pk).count() == 1
