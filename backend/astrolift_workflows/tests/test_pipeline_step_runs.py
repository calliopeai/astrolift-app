"""StepRun rows are created and settled from the pod's own records (#1501).

Nothing created a ``StepRun`` in production before this. The only
constructor was a test fixture, so ``AstroliftJobRun.stepRuns`` was an
empty list on every run and every per-step column held its default
forever.

These cover the half the shell script cannot: that the rows appear at
spawn, and that the records the script wrote become statuses an operator
can read — including for the steps that never ran, which leave no record
at all and would otherwise sit at ``pending`` for good.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization
from astrolift_pipelines.models import Job, JobRun, Pipeline, PipelineRun, Step, StepRun
from astrolift_workflows.activities.pipeline_job_spawn import _settle_step_runs

pytestmark = pytest.mark.django_db

NAMESPACE = "astrolift-pipelines-acme"
K8S_JOB = "pl-1-build"


class FakeClient:
    """Returns one pod for the Job, carrying *message* on its `main` container."""

    def __init__(self, message: str | None, *, pod: bool = True) -> None:
        self.message = message
        self.pod = pod

    def list(self, *, kind, namespace):  # noqa: ARG002 — mirrors the real signature
        if not self.pod:
            return []
        return [
            {
                "metadata": {"labels": {"job-name": K8S_JOB}},
                "status": {
                    "containerStatuses": [
                        {"name": "main", "state": {"terminated": {"message": self.message}}}
                    ]
                },
            },
            # A pod from something else in the same namespace, to prove the
            # label is what selects ours.
            {
                "metadata": {"labels": {"job-name": "pl-9-other"}},
                "status": {
                    "containerStatuses": [{"name": "main", "state": {"terminated": {"message": "0 ran 99"}}}]
                },
            },
        ]


class ExplodingClient:
    def list(self, *, kind, namespace):  # noqa: ARG002
        raise RuntimeError("cluster unreachable")


def _scaffold(suffix: str, step_specs: list[dict]):
    org = Organization.objects.create(name=f"Acme {suffix}", slug=f"acme-steps-{suffix}")
    pipeline = Pipeline.objects.create(
        organization=org,
        name=f"ci-{suffix}",
        repo_url="https://example.invalid/acme/shop",
        default_branch="main",
    )
    run = PipelineRun.objects.create(pipeline=pipeline, run_number=1, status=PipelineRun.Status.RUNNING)
    job = Job.objects.create(pipeline=pipeline, job_id="build", name="Build", container_image="alpine")
    job_run = JobRun.objects.create(pipeline_run=run, job=job, status=JobRun.Status.RUNNING)
    for spec in step_specs:
        step = Step.objects.create(job=job, **spec)
        StepRun.objects.create(job_run=job_run, step=step, status=StepRun.Status.PENDING)
    return job_run


def _statuses(job_run) -> list[str]:
    return [
        sr.status
        for sr in StepRun.objects.filter(job_run=job_run).select_related("step").order_by("step__position")
    ]


def test_every_step_settles_from_its_record():
    job_run = _scaffold(
        "a",
        [
            {"position": 0, "step_id": "install", "run": "npm ci"},
            {"position": 1, "step_id": "deploy", "uses": "astrolift/deploy@v1"},
            {"position": 2, "step_id": "test", "run": "npm test"},
        ],
    )

    failing = _settle_step_runs(
        job_run=job_run,
        client=FakeClient("0 ran 0\n1 skipped 0\n2 ran 0\n"),
        namespace=NAMESPACE,
        k8s_job_name=K8S_JOB,
        job_failed=False,
    )

    assert failing is None
    assert _statuses(job_run) == [
        StepRun.Status.SUCCESS,
        StepRun.Status.SKIPPED,
        StepRun.Status.SUCCESS,
    ]


def test_a_failed_step_carries_its_exit_code_and_the_rest_are_skipped():
    """The steps after a failure leave no record, because they never ran.

    Left at `pending` they would look like work still in flight on a run
    that finished; `skipped` is what actually happened to them.
    """
    job_run = _scaffold(
        "b",
        [
            {"position": 0, "run": "echo ok"},
            {"position": 1, "step_id": "build", "run": "make"},
            {"position": 2, "run": "echo never"},
        ],
    )

    failing = _settle_step_runs(
        job_run=job_run,
        client=FakeClient("0 ran 0\n1 ran 42\n"),
        namespace=NAMESPACE,
        k8s_job_name=K8S_JOB,
        job_failed=True,
    )

    assert failing == 42
    assert _statuses(job_run) == [
        StepRun.Status.SUCCESS,
        StepRun.Status.FAILURE,
        StepRun.Status.SKIPPED,
    ]
    failed = StepRun.objects.get(job_run=job_run, step__position=1)
    assert failed.exit_code == 42


def test_an_unreadable_pod_still_settles_from_the_jobs_verdict():
    """Step detail is diagnostics. Failing to read it must not leave a
    finished run showing steps that look like they are still going."""
    job_run = _scaffold("c", [{"position": 0, "run": "true"}, {"position": 1, "run": "true"}])

    _settle_step_runs(
        job_run=job_run,
        client=ExplodingClient(),
        namespace=NAMESPACE,
        k8s_job_name=K8S_JOB,
        job_failed=False,
    )

    assert _statuses(job_run) == [StepRun.Status.SUCCESS, StepRun.Status.SUCCESS]


def test_a_failed_job_with_no_records_marks_its_steps_failed_not_pending():
    job_run = _scaffold("d", [{"position": 0, "run": "true"}])

    _settle_step_runs(
        job_run=job_run,
        client=FakeClient(None, pod=False),
        namespace=NAMESPACE,
        k8s_job_name=K8S_JOB,
        job_failed=True,
    )

    assert _statuses(job_run) == [StepRun.Status.FAILURE]


def test_the_pod_is_selected_by_its_job_label():
    # The namespace is shared by every pipeline the org runs, so reading
    # "the first pod" would settle this job from another job's exit codes.
    job_run = _scaffold("e", [{"position": 0, "run": "true"}])

    failing = _settle_step_runs(
        job_run=job_run,
        client=FakeClient("0 ran 0\n"),
        namespace=NAMESPACE,
        k8s_job_name=K8S_JOB,
        job_failed=False,
    )

    assert failing is None
    assert _statuses(job_run) == [StepRun.Status.SUCCESS]


def test_a_run_with_no_steps_is_not_an_error():
    job_run = _scaffold("f", [])

    assert (
        _settle_step_runs(
            job_run=job_run,
            client=ExplodingClient(),
            namespace=NAMESPACE,
            k8s_job_name=K8S_JOB,
            job_failed=False,
        )
        is None
    )
