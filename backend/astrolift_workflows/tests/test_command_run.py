"""Tests for CommandRun + scheduled-job policy (#142, spec 06 §4.20)."""

from __future__ import annotations

import pytest

from astrolift_workflows.command_run import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    MAX_COMMAND_TIMEOUT_SECONDS,
    CommandRunError,
    CommandRunStatus,
    ConcurrencyError,
    ScheduledJobRun,
    assert_no_concurrent_run,
    classify_pod_failure,
    normalize_timeout,
    plan_job_spec,
)


# ---- timeout normalization -----------------------------------------


def test_default_timeout_when_none():
    assert normalize_timeout(None) == DEFAULT_COMMAND_TIMEOUT_SECONDS


def test_explicit_timeout_passes_through():
    assert normalize_timeout(120) == 120


def test_timeout_clamped_to_max():
    """Beyond 1hr the workflow can't reasonably hold the
    Temporal activity heartbeat — clamp."""
    assert normalize_timeout(MAX_COMMAND_TIMEOUT_SECONDS + 1) == MAX_COMMAND_TIMEOUT_SECONDS
    assert normalize_timeout(99999) == MAX_COMMAND_TIMEOUT_SECONDS


def test_negative_timeout_rejected():
    with pytest.raises(CommandRunError):
        normalize_timeout(-1)
    with pytest.raises(CommandRunError):
        normalize_timeout(0)


def test_timeout_constants():
    assert DEFAULT_COMMAND_TIMEOUT_SECONDS == 5 * 60
    assert MAX_COMMAND_TIMEOUT_SECONDS == 60 * 60


# ---- plan_job_spec -------------------------------------------------


def test_job_name_format():
    plan = plan_job_spec(
        app_slug="api", run_id="abc-123",
        namespace="acme-api", image="api:1.0",
        command=("python", "manage.py", "migrate"),
        env={},
    )
    assert plan.job_name == "cmd-api-abc-123"


def test_plan_carries_command_and_env():
    plan = plan_job_spec(
        app_slug="api", run_id="x", namespace="ns", image="img",
        command=("ls", "-la"),
        env={"DATABASE_URL": "postgres://...", "DEBUG": "1"},
    )
    assert plan.command == ("ls", "-la")
    assert plan.env["DEBUG"] == "1"


def test_plan_default_timeout():
    plan = plan_job_spec(
        app_slug="api", run_id="x", namespace="ns", image="img",
        command=("echo", "hi"), env={},
    )
    assert plan.timeout_seconds == DEFAULT_COMMAND_TIMEOUT_SECONDS


def test_plan_no_retries():
    """Spec rule: Run-Now is one-shot. If user clicked 'Run Now'
    they want it to complete or fail visibly, not silently
    retry."""
    plan = plan_job_spec(
        app_slug="api", run_id="x", namespace="ns", image="img",
        command=("echo", "hi"), env={},
    )
    assert plan.backoff_limit == 0


def test_plan_rejects_empty_required_fields():
    with pytest.raises(CommandRunError, match="required"):
        plan_job_spec(
            app_slug="", run_id="x", namespace="ns", image="img",
            command=("echo",), env={},
        )
    with pytest.raises(CommandRunError, match="required"):
        plan_job_spec(
            app_slug="api", run_id="x", namespace="ns", image="",
            command=("echo",), env={},
        )


def test_plan_rejects_empty_command():
    with pytest.raises(CommandRunError, match="command"):
        plan_job_spec(
            app_slug="api", run_id="x", namespace="ns", image="img",
            command=(), env={},
        )


def test_plan_rejects_empty_run_id():
    """Without a run id, the Job name collapses to 'cmd-app-' —
    not unique enough."""
    with pytest.raises(CommandRunError, match="run_id"):
        plan_job_spec(
            app_slug="api", run_id="", namespace="ns", image="img",
            command=("echo",), env={},
        )


# ---- classify_pod_failure ------------------------------------------


def test_deadline_exceeded_classified_as_timeout():
    """Spec rule: the Job's activeDeadlineSeconds elapsed."""
    out = classify_pod_failure(
        exit_code=137, pod_phase="Failed",
        reason="DeadlineExceeded", deadline_exceeded=True,
    )
    assert out == CommandRunStatus.FAILED_TIMEOUT


def test_oomkilled_classified_as_oom():
    """OOMKilled is a distinct outcome — operator action is to
    bump memory limits, not retry."""
    out = classify_pod_failure(
        exit_code=137, pod_phase="Failed",
        reason="OOMKilled", deadline_exceeded=False,
    )
    assert out == CommandRunStatus.FAILED_OOM


@pytest.mark.parametrize("reason", [
    "ImagePullBackOff", "ErrImagePull", "InvalidImageName",
])
def test_image_pull_failures_classified(reason):
    out = classify_pod_failure(
        exit_code=None, pod_phase="Pending",
        reason=reason, deadline_exceeded=False,
    )
    assert out == CommandRunStatus.FAILED_IMAGE_PULL


def test_zero_exit_classified_as_succeeded():
    out = classify_pod_failure(
        exit_code=0, pod_phase="Succeeded",
        reason="", deadline_exceeded=False,
    )
    assert out == CommandRunStatus.SUCCEEDED


def test_succeeded_phase_alone_is_enough():
    """Pod phase Succeeded with no exit code captured (rare race)
    is still success."""
    out = classify_pod_failure(
        exit_code=None, pod_phase="Succeeded",
        reason="", deadline_exceeded=False,
    )
    assert out == CommandRunStatus.SUCCEEDED


def test_other_non_zero_exit_is_generic_failed():
    out = classify_pod_failure(
        exit_code=1, pod_phase="Failed",
        reason="Error", deadline_exceeded=False,
    )
    assert out == CommandRunStatus.FAILED


def test_deadline_takes_precedence_over_oom():
    """If both flags set (rare race during pod kill), deadline
    is the more meaningful signal — operator response is
    'increase timeout', not 'increase memory'."""
    out = classify_pod_failure(
        exit_code=137, pod_phase="Failed",
        reason="OOMKilled", deadline_exceeded=True,
    )
    assert out == CommandRunStatus.FAILED_TIMEOUT


# ---- concurrency guard ---------------------------------------------


def _run(run_id: str, status: CommandRunStatus) -> ScheduledJobRun:
    return ScheduledJobRun(run_id=run_id, status=status, started_at_unix=0)


def test_no_concurrent_when_no_in_flight():
    assert_no_concurrent_run(
        cron_job_name="nightly-cleanup",
        in_flight_runs=[
            _run("r1", CommandRunStatus.SUCCEEDED),
            _run("r2", CommandRunStatus.FAILED_OOM),
        ],
    )


def test_concurrent_blocks_on_pending_run():
    with pytest.raises(ConcurrencyError, match="in-flight"):
        assert_no_concurrent_run(
            cron_job_name="nightly-cleanup",
            in_flight_runs=[_run("r1", CommandRunStatus.PENDING)],
        )


def test_concurrent_blocks_on_running_run():
    with pytest.raises(ConcurrencyError, match="in-flight"):
        assert_no_concurrent_run(
            cron_job_name="nightly-cleanup",
            in_flight_runs=[_run("r1", CommandRunStatus.RUNNING)],
        )


def test_allow_concurrent_bypasses_guard():
    """Operator opt-in via CronJob.allow_concurrent. Workflow
    skips the check."""
    assert_no_concurrent_run(
        cron_job_name="parallel-friendly",
        in_flight_runs=[_run("r1", CommandRunStatus.RUNNING)],
        allow_concurrent=True,
    )
