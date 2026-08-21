"""A pipeline run finally has jobs (#1531).

Nothing created a `Job` row before `job_sync`. The fan-out queried for
them, got nothing, and marked every run SUCCESS. These cover both shapes
the definition can take and, more importantly, the seam between them:
the two query sites in `pipeline_job_spawn` must resolve the mode rather
than filter by pipeline, or the second snapshot run raises.
"""

from __future__ import annotations

import itertools

import pytest

from astrolift_ci_convert.common.types import JobDef, PipelineDef, StepDef
from astrolift_identity.models import Organization
from astrolift_pipelines.job_sync import (
    SHARED,
    SNAPSHOT,
    job_for_run,
    jobs_for_run,
    mode_for,
    sync_definition,
)
from astrolift_pipelines.models import Job, Pipeline, PipelineRun, Step

pytestmark = pytest.mark.django_db


def _definition(*job_ids: str, steps: int = 1, needs: dict | None = None) -> PipelineDef:
    needs = needs or {}
    return PipelineDef(
        name="ci",
        jobs=[
            JobDef(
                job_id=job_id,
                name=job_id.title(),
                runs_on="astrolift/default",
                needs=list(needs.get(job_id, [])),
                steps=[StepDef(name=f"{job_id}-{i}", run=f"echo {job_id} {i}") for i in range(steps)],
            )
            for job_id in job_ids
        ],
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-jobsync")


@pytest.fixture
def pipeline(org):
    return Pipeline.objects.create(organization=org, name="ci", repo_url="https://github.com/a/b")


_next_run_number = itertools.count(1)


def _run(pipeline, sha: str = "aaa") -> PipelineRun:
    return PipelineRun.objects.create(
        pipeline=pipeline,
        run_number=next(_next_run_number),
        commit_sha=sha,
        trigger_ref="main",
    )


# ---- snapshot, the default ------------------------------------------------


def test_snapshot_is_the_default(org, pipeline):
    assert mode_for(_run(pipeline)) == SNAPSHOT


def test_a_run_gets_its_own_jobs_and_steps(pipeline):
    run = _run(pipeline)

    sync_definition(run, _definition("build", "test", steps=2))

    jobs = list(jobs_for_run(run).order_by("job_id"))
    assert [j.job_id for j in jobs] == ["build", "test"]
    assert all(j.pipeline_run_id == run.pk for j in jobs)
    for job in jobs:
        steps = list(Step.objects.filter(job=job, deleted_at__isnull=True).order_by("position"))
        assert [s.position for s in steps] == [0, 1]
        assert steps[0].run == f"echo {job.job_id} 0"


def test_two_runs_of_the_same_pipeline_keep_separate_jobs(pipeline):
    """The case that makes a pipeline-scoped lookup raise."""
    first, second = _run(pipeline, "aaa"), _run(pipeline, "bbb")

    sync_definition(first, _definition("build"))
    sync_definition(second, _definition("build", "deploy"))

    assert [j.job_id for j in jobs_for_run(first)] == ["build"]
    assert sorted(j.job_id for j in jobs_for_run(second)) == ["build", "deploy"]
    # Filtering by pipeline alone — what the code did before — now finds two.
    assert Job.objects.filter(pipeline=pipeline, job_id="build").count() == 2
    # And the resolver still returns exactly one per run.
    assert job_for_run(first, "build").pipeline_run_id == first.pk
    assert job_for_run(second, "build").pipeline_run_id == second.pk


def test_an_earlier_run_keeps_its_definition_when_the_toml_changes(pipeline):
    """The reason snapshot is the default: history stays true."""
    old = _run(pipeline, "aaa")
    sync_definition(old, _definition("build", "test"))

    new = _run(pipeline, "bbb")
    sync_definition(new, _definition("build"))

    assert sorted(j.job_id for j in jobs_for_run(old)) == ["build", "test"]


def test_re_syncing_a_run_does_not_double_its_jobs(pipeline):
    """Activities get retried. A retry must not double the fan-out."""
    run = _run(pipeline)

    sync_definition(run, _definition("build", "test"))
    sync_definition(run, _definition("build", "test"))

    assert jobs_for_run(run).count() == 2
    job = job_for_run(run, "build")
    assert Step.objects.filter(job=job, deleted_at__isnull=True).count() == 1


# ---- shared ---------------------------------------------------------------


@pytest.fixture
def shared_pipeline(org):
    org.pipeline_definition_mode = SHARED
    org.save(update_fields=["pipeline_definition_mode", "updated_at", "version"])
    return Pipeline.objects.create(organization=org, name="ci", repo_url="https://github.com/a/b")


def test_shared_rows_are_reused_across_runs(shared_pipeline):
    first, second = _run(shared_pipeline, "aaa"), _run(shared_pipeline, "bbb")

    sync_definition(first, _definition("build"))
    original_pk = job_for_run(first, "build").pk
    sync_definition(second, _definition("build"))

    assert mode_for(second) == SHARED
    # The same row, not a second one: a JobRun from the first run still
    # points at something live.
    assert job_for_run(second, "build").pk == original_pk
    assert Job.objects.filter(pipeline=shared_pipeline, deleted_at__isnull=True).count() == 1


def test_shared_rows_track_a_changed_definition(shared_pipeline):
    run = _run(shared_pipeline)
    sync_definition(run, _definition("build", "test"))

    sync_definition(_run(shared_pipeline, "bbb"), _definition("build", "deploy"))

    live = sorted(j.job_id for j in Job.objects.filter(pipeline=shared_pipeline, deleted_at__isnull=True))
    assert live == ["build", "deploy"]
    # Soft-deleted rather than dropped, per the never-hard-delete rule —
    # `Job.objects` already hides it, so this has to ask `all_objects`.
    assert Job.all_objects.filter(pipeline=shared_pipeline, job_id="test", deleted_at__isnull=False).exists()


def test_shared_steps_are_replaced_not_appended(shared_pipeline):
    run = _run(shared_pipeline)
    sync_definition(run, _definition("build", steps=3))

    sync_definition(_run(shared_pipeline, "bbb"), _definition("build", steps=1))

    job = job_for_run(run, "build")
    assert Step.objects.filter(job=job, deleted_at__isnull=True).count() == 1


def test_shared_mode_does_not_see_snapshot_rows(org, pipeline, shared_pipeline):
    """Two pipelines in one org after a mode flip; neither set leaks."""
    snap_run = _run(pipeline)
    org.pipeline_definition_mode = SNAPSHOT
    org.save(update_fields=["pipeline_definition_mode", "updated_at", "version"])
    sync_definition(snap_run, _definition("build"))

    org.pipeline_definition_mode = SHARED
    org.save(update_fields=["pipeline_definition_mode", "updated_at", "version"])
    shared_run = _run(shared_pipeline)
    sync_definition(shared_run, _definition("build"))

    assert jobs_for_run(shared_run).count() == 1
    assert jobs_for_run(shared_run).first().pipeline_run_id is None


# ---- the definition survives the round trip it will really take -----------


def test_a_real_toml_reaches_job_rows(pipeline):
    """End to end from text, which is what the workflow now does."""
    from astrolift_ci_convert.common.toml_reader import read_toml

    run = _run(pipeline)
    sync_definition(
        run,
        read_toml(
            'name = "ci"\n'
            '[jobs.build]\nname = "Build"\ncontainer = "python:3.12"\n'
            '[[jobs.build.steps]]\nrun = "make"\n'
            '[jobs.test]\nneeds = ["build"]\n'
            '[[jobs.test.steps]]\nuses = "astrolift/checkout"\n'
        ),
    )

    build = job_for_run(run, "build")
    assert build.container_image == "python:3.12"
    assert job_for_run(run, "test").needs == ["build"]
    assert Step.objects.get(job=build, deleted_at__isnull=True).run.strip() == "make"
