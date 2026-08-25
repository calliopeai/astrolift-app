"""CommandRunWorkflow and RunScheduledJobWorkflow exist and are wired (#1600).

`astrolift_workflows.command_run` opens by naming both and describing what
each does. Neither existed, so its policy -- timeout bounds, the Job spec
plan, failure classification, the concurrency guard -- had no caller, and the
`CommandRun` / `ScheduledJobRun` models, their admin and their GraphQL types
had nothing writing terminal states.

The registration tests are the ones that would have caught the original
absence, and they are the cheap half. A workflow that exists but is not in
the worker's `WORKFLOWS` tuple is exactly as dead as one that does not exist
-- #1580 opens with `PipelineRunWorkflow` in precisely that state.
"""

from __future__ import annotations

import pytest


def test_both_workflows_exist():
    from astrolift_workflows.workflows import CommandRunWorkflow, RunScheduledJobWorkflow

    assert CommandRunWorkflow is not None
    assert RunScheduledJobWorkflow is not None


def test_both_are_registered_with_the_worker():
    """The half that makes them reachable. #1580's opening finding was a
    workflow that existed and was never registered, so it could not run."""
    from astrolift_workflows.worker import WORKFLOWS
    from astrolift_workflows.workflows import CommandRunWorkflow, RunScheduledJobWorkflow

    assert CommandRunWorkflow in WORKFLOWS
    assert RunScheduledJobWorkflow in WORKFLOWS


def test_their_activities_are_registered():
    """A registered workflow whose activities are not is a workflow that
    starts and then fails on its first step."""
    from astrolift_workflows.activities.command_run_exec import (
        assert_no_concurrent,
        finish_command_run,
        poll_command_run,
        start_command_run,
    )
    from astrolift_workflows.worker import ACTIVITIES

    for fn in (start_command_run, poll_command_run, finish_command_run, assert_no_concurrent):
        assert fn in ACTIVITIES, f"{fn.__name__} is not registered"


# ---- the Job the plan renders -------------------------------------------


def _plan():
    from astrolift_workflows.command_run import plan_job_spec

    return plan_job_spec(
        app_slug="hello",
        run_id="abc12345",
        namespace="acme-hello",
        image="ghcr.io/acme/hello:v2",
        command=["python", "manage.py", "migrate"],
        env={"ENV": "prod"},
        timeout_seconds=120,
    )


def test_the_manifest_carries_the_plans_timeout_as_the_deadline():
    """`activeDeadlineSeconds`, not a workflow timer. Kubernetes is what
    actually kills the pod, so a second timeout could only disagree."""
    from astrolift_workflows.activities.command_run_exec import _render_job_manifest

    manifest = _render_job_manifest(_plan())

    assert manifest["spec"]["activeDeadlineSeconds"] == 120


def test_the_job_does_not_retry():
    """`backoffLimit: 0`. The plan's own docstring: if the user hit Run Now
    they want it to complete or fail visibly, not silently retry."""
    from astrolift_workflows.activities.command_run_exec import _render_job_manifest

    assert _render_job_manifest(_plan())["spec"]["backoffLimit"] == 0


def test_the_job_cleans_itself_up():
    from astrolift_workflows.activities.command_run_exec import _render_job_manifest

    assert _render_job_manifest(_plan())["spec"]["ttlSecondsAfterFinished"] > 0


def test_the_command_is_passed_verbatim():
    from astrolift_workflows.activities.command_run_exec import _render_job_manifest

    container = _render_job_manifest(_plan())["spec"]["template"]["spec"]["containers"][0]

    assert container["command"] == ["python", "manage.py", "migrate"]
    assert container["image"] == "ghcr.io/acme/hello:v2"


def test_the_pod_never_restarts():
    """`restartPolicy: Never` alongside `backoffLimit: 0` -- both are needed;
    a restarting pod re-runs the command inside a Job that thinks it is on
    its first attempt."""
    from astrolift_workflows.activities.command_run_exec import _render_job_manifest

    spec = _render_job_manifest(_plan())["spec"]["template"]["spec"]

    assert spec["restartPolicy"] == "Never"


# ---- the concurrency guard ----------------------------------------------


def test_a_second_run_is_refused_by_default():
    from astrolift_workflows.command_run import (
        CommandRunStatus,
        ConcurrencyError,
        ScheduledJobRun,
        assert_no_concurrent_run,
    )

    in_flight = [ScheduledJobRun(run_id="r1", status=CommandRunStatus.RUNNING, started_at_unix=0)]

    with pytest.raises(ConcurrencyError):
        assert_no_concurrent_run(cron_job_name="nightly", in_flight_runs=in_flight)


def test_allow_concurrent_lets_it_through():
    from astrolift_workflows.command_run import (
        CommandRunStatus,
        ScheduledJobRun,
        assert_no_concurrent_run,
    )

    in_flight = [ScheduledJobRun(run_id="r1", status=CommandRunStatus.RUNNING, started_at_unix=0)]

    assert_no_concurrent_run(cron_job_name="nightly", in_flight_runs=in_flight, allow_concurrent=True)
