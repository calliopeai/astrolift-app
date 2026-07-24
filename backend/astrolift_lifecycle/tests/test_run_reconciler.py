"""Tests for the run-status reconciler (real Postgres).

Covers the sweep's core behaviour with an injected fake ManagementBackend
(never a real cluster):

* a RUNNING ScheduledJobRun advances to succeeded / failed from the Job
  status, stamping started/ended/duration/exit_code (+ log_excerpt);
* a PENDING TaskRun advances PENDING -> RUNNING -> succeeded (and never
  touches a log field it doesn't have);
* terminal rows are never selected or rewritten (idempotent);
* a read failure (unreachable cluster / missing Job) or a run with no
  resolvable cluster is a safe no-op that leaves the row exactly as-is;
* rows with no ``k8s_job_name`` are ignored;
* the summary dict shape.

The fake is injected through ``core.cluster_management`` so the reconciler
resolves it exactly the way the workflow activities do — no real apiserver.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

import pytest
from _sdk.cluster import JobStatus
from django.utils import timezone

from astrolift_lifecycle.models.jobs import ScheduledJobRun
from astrolift_lifecycle.models.task_run import TaskRun
from astrolift_lifecycle.services.run_reconciler import reconcile_runs
from astrolift_registry.models import Workload
from core.cluster_management import (
    reset_management_backend_for_tests,
    set_management_backend_for_tests,
)

pytestmark = pytest.mark.django_db


# ---- fake management backend ---------------------------------------


class _FakeMgmtBackend:
    """Deterministic ManagementBackend — only ``read_job_status`` is
    exercised by the reconciler. Stage a JobStatus (or an Exception to
    raise) per k8s job name; unknown jobs return an empty status."""

    def __init__(self) -> None:
        self.by_job: dict[str, Any] = {}
        self.default: JobStatus = JobStatus()
        self.calls: list[tuple[str, str]] = []

    def read_job_status(self, *, auth: Any, namespace: str, job_name: str) -> JobStatus:
        self.calls.append((namespace, job_name))
        outcome = self.by_job.get(job_name, self.default)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def mgmt_backend():
    backend = _FakeMgmtBackend()
    set_management_backend_for_tests(backend)
    try:
        yield backend
    finally:
        reset_management_backend_for_tests()


# ---- row builders --------------------------------------------------


@pytest.fixture
def cronjob_workload(app):
    return Workload.objects.create(
        registered_app=app,
        name="nightly",
        slug="nightly",
        kind=Workload.Kind.CRONJOB,
        schedule="0 * * * *",
    )


@pytest.fixture
def task_workload(app):
    return Workload.objects.create(
        registered_app=app,
        name="migrate",
        slug="migrate",
        kind=Workload.Kind.TASK,
    )


def _scheduled_run(workload, env, **kw) -> ScheduledJobRun:
    defaults = {
        "workload": workload,
        "app_environment": env,
        "k8s_job_name": "nightly-manual-abc123",
        "namespace": "acme-test-hello-app",
        "status": ScheduledJobRun.Status.RUNNING,
        "trigger_kind": ScheduledJobRun.TriggerKind.MANUAL,
    }
    return ScheduledJobRun.objects.create(**{**defaults, **kw})


def _task_run(workload, env, **kw) -> TaskRun:
    defaults = {
        "workload": workload,
        "app_environment": env,
        "k8s_job_name": "task-xyz789",
        "status": TaskRun.Status.PENDING,
        "command": ["python", "manage.py", "migrate"],
    }
    return TaskRun.objects.create(**{**defaults, **kw})


# ---- ScheduledJobRun transitions -----------------------------------


def test_scheduled_running_to_succeeded(mgmt_backend, cronjob_workload, env):
    start = timezone.now() - _dt.timedelta(seconds=42)
    end = timezone.now()
    run = _scheduled_run(cronjob_workload, env)
    mgmt_backend.by_job[run.k8s_job_name] = JobStatus(
        succeeded=1,
        start_time=start,
        completion_time=end,
    )

    summary = reconcile_runs()

    run.refresh_from_db()
    assert run.status == ScheduledJobRun.Status.SUCCEEDED
    assert run.started_at == start
    assert run.ended_at == end
    assert run.duration_seconds == 42
    assert run.exit_code == 0
    assert summary["evaluated"] == 1
    assert summary["succeeded"] == 1
    # The backend was consulted with the row's namespace + job name.
    assert mgmt_backend.calls == [("acme-test-hello-app", run.k8s_job_name)]


def test_scheduled_running_to_failed_records_log_excerpt(mgmt_backend, cronjob_workload, env):
    start = timezone.now() - _dt.timedelta(seconds=10)
    run = _scheduled_run(cronjob_workload, env)
    mgmt_backend.by_job[run.k8s_job_name] = JobStatus(
        failed=1,
        start_time=start,
        conditions=("Job has reached the specified backoff limit",),
    )

    summary = reconcile_runs()

    run.refresh_from_db()
    assert run.status == ScheduledJobRun.Status.FAILED
    assert run.exit_code == 1
    # No completionTime on failure -> ended stamped ~now so duration exists.
    assert run.ended_at is not None
    assert run.duration_seconds is not None and run.duration_seconds >= 0
    assert "backoff limit" in run.log_excerpt
    assert summary["failed"] == 1


def test_terminal_scheduled_run_is_never_touched(mgmt_backend, cronjob_workload, env):
    run = _scheduled_run(
        cronjob_workload,
        env,
        status=ScheduledJobRun.Status.SUCCEEDED,
        exit_code=0,
    )
    before_version = run.version
    # Even if the fake would report failure, a terminal row is out of scope.
    mgmt_backend.by_job[run.k8s_job_name] = JobStatus(failed=1)

    summary = reconcile_runs()

    run.refresh_from_db()
    assert run.status == ScheduledJobRun.Status.SUCCEEDED
    assert run.exit_code == 0
    assert run.version == before_version  # no write at all
    assert summary["evaluated"] == 0
    assert mgmt_backend.calls == []  # terminal row never read


def test_scheduled_run_without_job_name_is_ignored(mgmt_backend, cronjob_workload, env):
    run = _scheduled_run(cronjob_workload, env, k8s_job_name="")

    summary = reconcile_runs()

    run.refresh_from_db()
    assert run.status == ScheduledJobRun.Status.RUNNING
    assert summary["evaluated"] == 0
    assert mgmt_backend.calls == []


# ---- safety: read failure / no cluster -----------------------------


def test_read_failure_is_safe_noop(mgmt_backend, cronjob_workload, env):
    """Unreachable cluster / missing Job -> the read raises; the row is
    left exactly as-is and counted as an error, never a crash."""
    run = _scheduled_run(cronjob_workload, env)
    before_version = run.version
    mgmt_backend.by_job[run.k8s_job_name] = RuntimeError("404 job not found")

    summary = reconcile_runs()

    run.refresh_from_db()
    assert run.status == ScheduledJobRun.Status.RUNNING
    assert run.version == before_version
    assert summary["evaluated"] == 1
    assert summary["errors"] == 1
    assert summary["succeeded"] == 0
    assert summary["failed"] == 0


def test_empty_job_status_leaves_row_running(mgmt_backend, cronjob_workload, env):
    """A Job whose pod hasn't scheduled (empty status) is 'nothing yet' —
    the RUNNING row stays RUNNING with no write."""
    run = _scheduled_run(cronjob_workload, env)
    before_version = run.version
    mgmt_backend.by_job[run.k8s_job_name] = JobStatus()  # all zero, no times

    summary = reconcile_runs()

    run.refresh_from_db()
    assert run.status == ScheduledJobRun.Status.RUNNING
    assert run.version == before_version
    assert summary["unchanged"] == 1


# ---- TaskRun transitions -------------------------------------------


def test_task_pending_to_running_then_succeeded(mgmt_backend, task_workload, env):
    run = _task_run(task_workload, env)
    start = timezone.now() - _dt.timedelta(seconds=5)

    # Tick 1: Job active -> PENDING advances to RUNNING + started_at stamped.
    mgmt_backend.by_job[run.k8s_job_name] = JobStatus(active=1, start_time=start)
    summary1 = reconcile_runs()
    run.refresh_from_db()
    assert run.status == TaskRun.Status.RUNNING
    assert run.started_at == start
    assert summary1["running"] == 1

    # Tick 2: Job succeeded -> RUNNING advances to SUCCEEDED.
    end = timezone.now()
    mgmt_backend.by_job[run.k8s_job_name] = JobStatus(
        succeeded=1,
        start_time=start,
        completion_time=end,
    )
    summary2 = reconcile_runs()
    run.refresh_from_db()
    assert run.status == TaskRun.Status.SUCCEEDED
    assert run.ended_at == end
    assert run.exit_code == 0
    assert run.duration_seconds is not None and run.duration_seconds >= 0
    assert summary2["succeeded"] == 1


def test_task_run_without_environment_is_skipped(mgmt_backend, task_workload):
    """TaskRun.app_environment is nullable — no env means no resolvable
    cluster, so the row is skipped (never read, never crashed)."""
    run = _task_run(task_workload, env=None)
    before_version = run.version

    summary = reconcile_runs()

    run.refresh_from_db()
    assert run.status == TaskRun.Status.PENDING
    assert run.version == before_version
    assert summary["skipped"] == 1
    assert mgmt_backend.calls == []


def test_task_run_terminal_is_untouched(mgmt_backend, task_workload, env):
    run = _task_run(task_workload, env, status=TaskRun.Status.CANCELLED)
    before_version = run.version
    mgmt_backend.by_job[run.k8s_job_name] = JobStatus(succeeded=1)

    summary = reconcile_runs()

    run.refresh_from_db()
    assert run.status == TaskRun.Status.CANCELLED
    assert run.version == before_version
    assert summary["evaluated"] == 0


# ---- idempotency + summary -----------------------------------------


def test_idempotent_second_sweep_is_noop_after_terminal(mgmt_backend, cronjob_workload, env):
    run = _scheduled_run(cronjob_workload, env)
    mgmt_backend.by_job[run.k8s_job_name] = JobStatus(succeeded=1, start_time=timezone.now())

    reconcile_runs()
    run.refresh_from_db()
    version_after_first = run.version
    assert run.status == ScheduledJobRun.Status.SUCCEEDED

    # Second sweep: the row is now terminal, so it's out of the query.
    summary2 = reconcile_runs()
    run.refresh_from_db()
    assert run.version == version_after_first
    assert summary2["evaluated"] == 0


def test_summary_reports_all_buckets(mgmt_backend, cronjob_workload, task_workload, env):
    ok = _scheduled_run(cronjob_workload, env, k8s_job_name="ok-job")
    bad = _scheduled_run(cronjob_workload, env, k8s_job_name="bad-job")
    task = _task_run(task_workload, env, k8s_job_name="task-job")
    mgmt_backend.by_job["ok-job"] = JobStatus(succeeded=1, start_time=timezone.now())
    mgmt_backend.by_job["bad-job"] = RuntimeError("unreachable")
    mgmt_backend.by_job["task-job"] = JobStatus(active=1, start_time=timezone.now())

    summary = reconcile_runs()

    assert summary["evaluated"] == 3
    assert summary["succeeded"] == 1
    assert summary["errors"] == 1
    assert summary["running"] == 1
    assert set(summary) == {
        "evaluated",
        "succeeded",
        "failed",
        "running",
        "unchanged",
        "skipped",
        "errors",
    }
    assert ok and bad and task  # rows referenced


# ---- activity glue -------------------------------------------------


def test_activity_sync_maps_summary(mgmt_backend, cronjob_workload, env):
    from astrolift_workflows.activities.run_status_reconcile import (
        RunStatusReconcileSummary,
        _reconcile_runs_tick_sync,
    )

    run = _scheduled_run(cronjob_workload, env)
    mgmt_backend.by_job[run.k8s_job_name] = JobStatus(succeeded=1, start_time=timezone.now())

    result = _reconcile_runs_tick_sync()

    assert isinstance(result, RunStatusReconcileSummary)
    assert result.evaluated == 1
    assert result.succeeded == 1
