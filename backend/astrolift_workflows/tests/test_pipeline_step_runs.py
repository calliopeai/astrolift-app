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
from astrolift_workflows.activities.pipeline_job_spawn import _read_job_pod, _settle_step_runs

pytestmark = pytest.mark.django_db

NAMESPACE = "astrolift-pipelines-acme"
K8S_JOB = "pl-1-build"


def _pod(message: str | None):
    """The Job's pod, carrying *message* on its `main` container."""
    return {
        "metadata": {"name": "pl-1-build-abcde", "labels": {"job-name": K8S_JOB}},
        "status": {"containerStatuses": [{"name": "main", "state": {"terminated": {"message": message}}}]},
    }


class FakeClient:
    """Lists the Job's pod alongside another job's, in the shared namespace."""

    def __init__(self, message: str | None, *, pod: bool = True) -> None:
        self.message = message
        self.pod = pod

    def list(self, *, kind, namespace):  # noqa: ARG002 — mirrors the real signature
        others = [
            {
                "metadata": {"name": "pl-9-other-zzzzz", "labels": {"job-name": "pl-9-other"}},
                "status": {
                    "containerStatuses": [{"name": "main", "state": {"terminated": {"message": "0 ran 99"}}}]
                },
            }
        ]
        return ([_pod(self.message)] if self.pod else []) + others


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
        pod=_pod("0 ran 0\n1 skipped 0\n2 ran 0\n"),
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
        pod=_pod("0 ran 0\n1 ran 42\n"),
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
        pod=None,
        job_failed=False,
    )

    assert _statuses(job_run) == [StepRun.Status.SUCCESS, StepRun.Status.SUCCESS]


def test_a_failed_job_with_no_records_marks_its_steps_failed_not_pending():
    job_run = _scaffold("d", [{"position": 0, "run": "true"}])

    _settle_step_runs(
        job_run=job_run,
        pod=None,
        job_failed=True,
    )

    assert _statuses(job_run) == [StepRun.Status.FAILURE]


def test_the_pod_is_selected_by_its_job_label():
    # The namespace is shared by every pipeline the org runs, so reading
    # "the first pod" would settle this job from another job's exit codes.
    job_run = _scaffold("e", [{"position": 0, "run": "true"}])

    failing = _settle_step_runs(
        job_run=job_run,
        pod=_read_job_pod(FakeClient("0 ran 0\n"), NAMESPACE, K8S_JOB),
        job_failed=False,
    )

    assert failing is None
    assert _statuses(job_run) == [StepRun.Status.SUCCESS]


def test_the_pods_output_is_kept_on_the_job_run(monkeypatch):
    """#1218: nothing captured a pipeline's logs anywhere.

    The window is narrow and this is it — `poll_pipeline_job` deletes the
    Job with `propagation_policy="Foreground"` on both terminal branches,
    so the pod and its output go with it.
    """
    from astrolift_workflows.activities import pipeline_job_spawn as mod

    job_run = _scaffold("g", [{"position": 0, "run": "echo hi"}])
    monkeypatch.setattr(mod, "_resolve_cluster", lambda run: object())

    async def _fake_tail(*, cluster, namespace, pod_name, tail):  # noqa: ARG001
        return ["building…", "", "   ", "done"]

    monkeypatch.setattr("core.cluster_observability.fetch_pod_log_tail", _fake_tail)

    mod._capture_job_logs(job_run=job_run, run=job_run.pipeline_run, pod=_pod("0 ran 0\n"))

    job_run.refresh_from_db()
    # Blank lines dropped, order preserved.
    assert job_run.log_excerpt == "building…\ndone"


def test_a_log_read_that_fails_does_not_cost_the_run(monkeypatch):
    from astrolift_workflows.activities import pipeline_job_spawn as mod

    job_run = _scaffold("h", [{"position": 0, "run": "true"}])
    monkeypatch.setattr(mod, "_resolve_cluster", lambda run: (_ for _ in ()).throw(RuntimeError("gone")))

    mod._capture_job_logs(job_run=job_run, run=job_run.pipeline_run, pod=_pod("0 ran 0\n"))

    job_run.refresh_from_db()
    assert job_run.log_excerpt == ""


def test_a_long_build_is_truncated_from_the_front(monkeypatch):
    """The tail is what an operator needs — a failure is at the end."""
    from astrolift_workflows.activities import pipeline_job_spawn as mod

    job_run = _scaffold("i", [{"position": 0, "run": "true"}])
    monkeypatch.setattr(mod, "_resolve_cluster", lambda run: object())

    async def _fake_tail(*, cluster, namespace, pod_name, tail):  # noqa: ARG001
        return ["x" * 100 for _ in range(2000)] + ["THE-LAST-LINE"]

    monkeypatch.setattr("core.cluster_observability.fetch_pod_log_tail", _fake_tail)

    mod._capture_job_logs(job_run=job_run, run=job_run.pipeline_run, pod=_pod(""))

    job_run.refresh_from_db()
    assert len(job_run.log_excerpt) <= mod.PIPELINE_LOG_CHARS + 1
    assert job_run.log_excerpt.startswith("…")
    assert job_run.log_excerpt.endswith("THE-LAST-LINE")


def test_no_pod_means_nothing_to_capture(monkeypatch):
    from astrolift_workflows.activities import pipeline_job_spawn as mod

    job_run = _scaffold("j", [{"position": 0, "run": "true"}])

    mod._capture_job_logs(job_run=job_run, run=job_run.pipeline_run, pod=None)

    job_run.refresh_from_db()
    assert job_run.log_excerpt == ""


def test_a_cluster_that_will_not_list_pods_is_not_fatal():
    """`_read_job_pod` is the only place the k8s read happens now, and a
    settled run must not be lost to a cluster that stopped answering."""
    assert _read_job_pod(ExplodingClient(), NAMESPACE, K8S_JOB) is None


def test_a_run_with_no_steps_is_not_an_error():
    job_run = _scaffold("f", [])

    assert (
        _settle_step_runs(
            job_run=job_run,
            pod=None,
            job_failed=False,
        )
        is None
    )
